"""Единый интерфейс адаптеров генерации видео и озвучки.

Сценарный и монтажный модули работают только с этими типами, поэтому замена Kling на другой
сервис сводится к новому адаптеру.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from ..config import redact


class ProviderError(RuntimeError):
    """Ошибка провайдера. retryable — можно ли безопасно повторить запрос."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None, code: Any = None):
        super().__init__(redact(message))
        self.retryable = retryable
        self.status = status
        self.code = code


class AmbiguousSubmitError(ProviderError):
    """Запрос на создание платной задачи мог дойти до сервера, но ответ не получен.
    Повторять НЕЛЬЗЯ — сначала нужно поискать задачу по external_task_id."""


class NotConfiguredError(ProviderError):
    pass


@dataclass
class VideoRequest:
    kind: str                       # image2video | avatar | lipsync | text2video
    model: str
    prompt: str = ""
    negative_prompt: str = ""
    image: Path | None = None
    audio: Path | None = None
    video: Path | None = None
    duration: float = 5            # желаемая длительность клипа, сек
    resolution: str = "720p"
    mode: str = "std"
    external_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "model": self.model, "duration": self.duration, "resolution": self.resolution,
            "mode": self.mode, "prompt": self.prompt[:300], "image": str(self.image) if self.image else None,
            "audio": str(self.audio) if self.audio else None, **self.extra,
        }


def normalize_status(raw: str | None) -> str:
    """Приводит статус провайдера к нашему набору: submitted | processing | succeeded | failed."""
    v = (raw or "").strip().lower()
    if v in ("succeed", "succeeded", "success", "completed", "complete", "done"):
        return "succeeded"
    if v in ("failed", "fail", "error", "cancelled", "canceled"):
        return "failed"
    if v in ("submitted", "pending", "queued", "in_queue", "throttled"):
        return "submitted"
    return "processing"


@dataclass
class TaskState:
    task_id: str
    status: str                     # submitted | processing | succeeded | failed
    video_url: str | None = None
    message: str = ""
    billed_units: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status in ("succeeded", "failed")


class VideoProvider(ABC):
    name: str = "base"
    paid: bool = True
    # Какие типы задач поддерживает адаптер
    kinds: tuple[str, ...] = ()

    @abstractmethod
    def configured(self) -> tuple[bool, str]:
        """(готов ли адаптер, пояснение) — без сетевых запросов."""

    @abstractmethod
    def billed_duration(self, req: VideoRequest) -> float:
        """Длительность, за которую будет списана оплата (с учётом допустимых значений модели)."""

    @abstractmethod
    def estimate_usd(self, req: VideoRequest, pricing: dict) -> float:
        ...

    @abstractmethod
    def submit(self, req: VideoRequest) -> str:
        """Создаёт задачу, возвращает task_id провайдера."""

    @abstractmethod
    def poll(self, task_id: str, kind: str) -> TaskState:
        ...

    def find_by_external_id(self, external_id: str, kind: str) -> TaskState | None:
        """Поиск задачи по нашему идентификатору (для восстановления после сбоя)."""
        return None

    def account_info(self) -> dict[str, Any]:
        """Бесплатная проверка подключения / баланса. Не должна тратить кредиты."""
        return {}

    def download(self, url: str, dest: Path, timeout: int = 300) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(4):
            try:
                with requests.get(url, stream=True, timeout=timeout) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                tmp.replace(dest)
                return dest
            except requests.RequestException as e:  # скачивание бесплатно — можно повторять
                if attempt == 3:
                    raise ProviderError(f"Не удалось скачать результат: {e}", retryable=True) from e
                time.sleep(2 ** (attempt + 1))
        return dest


@dataclass
class TTSResult:
    audio_path: Path
    duration: float
    # Пословные тайминги [(слово, start, end)], если провайдер их отдаёт
    words: list[tuple[str, float, float]] = field(default_factory=list)
    characters: int = 0
    cached: bool = False


class TTSProvider(ABC):
    name: str = "base"
    paid: bool = True

    @abstractmethod
    def configured(self) -> tuple[bool, str]:
        ...

    @abstractmethod
    def synthesize(self, text: str, preset: dict, dest: Path) -> TTSResult:
        ...

    def estimate_usd(self, text: str, preset: dict, pricing: dict) -> float:
        return 0.0

    def account_info(self) -> dict[str, Any]:
        return {}


def http_json(method: str, url: str, *, headers: dict, json_body: Any = None, params: dict | None = None,
              timeout: int = 60, retries: int = 4, safe_to_retry: bool = True) -> dict:
    """HTTP-запрос с повторами.

    safe_to_retry=False для запросов, создающих ПЛАТНЫЕ задачи: повтор выполняется только если сервер
    явно ответил отказом без создания задачи (429 rate limit). Обрыв соединения/таймаут
    превращается в AmbiguousSubmitError.
    """
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            # allow_redirects=False: заголовки с ключом (xi-api-key и т.п.) не уходят на чужой хост
            r = requests.request(method, url, headers=headers, json=json_body, params=params, timeout=timeout,
                                 allow_redirects=False)
        except (requests.ConnectionError, requests.Timeout) as e:
            if not safe_to_retry:
                raise AmbiguousSubmitError(f"Нет ответа от {url}: {e}") from e
            last = e
            time.sleep(min(2 ** (attempt + 1), 30))
            continue
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {"raw": r.text[:500]}
        if r.status_code == 429 or (r.status_code >= 500 and safe_to_retry):
            last = ProviderError(f"HTTP {r.status_code}: {str(data)[:300]}", retryable=True, status=r.status_code)
            if attempt < retries:
                time.sleep(min(2 ** (attempt + 1), 30))
                continue
            raise last
        if r.status_code >= 500 and not safe_to_retry:
            raise AmbiguousSubmitError(f"HTTP {r.status_code} при создании задачи: {str(data)[:300]}",
                                       status=r.status_code)
        if 300 <= r.status_code < 400:
            raise ProviderError(f"Неожиданный редирект HTTP {r.status_code} с {url} — запрос остановлен", status=r.status_code)
        if r.status_code >= 400:
            raise ProviderError(f"HTTP {r.status_code}: {str(data)[:500]}", status=r.status_code,
                                code=(data or {}).get("code") if isinstance(data, dict) else None)
        return data if isinstance(data, dict) else {"data": data}
    raise ProviderError(f"Запрос не выполнен после повторов: {last}", retryable=True)
