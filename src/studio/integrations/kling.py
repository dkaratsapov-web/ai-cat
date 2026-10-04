"""Адаптер Kling AI API.

Источник: официальная документация https://kling.ai/document-api (страницы Authentication,
Video 2.6 Image to Video, Avatar, Account usage, Pricing; снимок от 2026-09-20).

Поддерживаемые задачи:
  * image2video — новый стандарт API: POST /image-to-video/{model}, статус GET /tasks?task_ids=...
  * avatar      — говорящий персонаж по фото + аудио: POST /v1/videos/avatar/image2video
                  (поддержка морды животного официально не подтверждена — проверяется тестом)

Авторизация: API Key (Authorization: Bearer <key>) — основной способ для всех моделей;
либо Access Key + Secret Key → JWT (HS256, iss/exp/nbf) — только для API «старого» стандарта.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import secret
from .base import (
    NotConfiguredError,
    ProviderError,
    TaskState,
    VideoProvider,
    VideoRequest,
    http_json,
    normalize_status,
)

DEFAULT_BASE = "https://api-singapore.klingai.com"

# Допустимые длительности по моделям (новый стандарт).
MODEL_DURATIONS: dict[str, list[int]] = {
    "kling-2.6": [5, 10],
    "kling-2.5-turbo": [5, 10],
    "kling-3.0-turbo": list(range(3, 16)),
}

AVATAR_MAX_AUDIO_BYTES = 5 * 1024 * 1024
IMAGE_MAX_BYTES = {"image2video": 50 * 1024 * 1024, "avatar": 10 * 1024 * 1024}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_jwt(ak: str, sk: str, ttl: int = 1800) -> str:
    """JWT по схеме из документации Kling (HS256; payload iss/exp/nbf)."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": ak, "exp": now + ttl, "nbf": now - 5}
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    sig = hmac.new(sk.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


def b64_file(path: Path, limit: int) -> str:
    data = Path(path).read_bytes()
    if len(data) > limit:
        raise ProviderError(f"Файл {Path(path).name} больше лимита {limit // (1024 * 1024)} МБ")
    return base64.b64encode(data).decode()


def to_mp3(audio: Path, min_seconds: float = 2.0) -> Path:
    """Готовит аудио для Avatar: mp3, не короче 2 с (требование API)."""
    fd, name = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)  # на Windows открытый дескриптор не даёт ffmpeg записать файл
    out = Path(name)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio),
         "-af", f"apad=whole_dur={min_seconds}", "-ac", "1", "-ar", "44100", "-b:a", "128k", str(out)],
        check=True,
    )
    return out


class KlingProvider(VideoProvider):
    name = "kling"
    kinds = ("image2video", "avatar")

    def __init__(self, settings=None):
        self.settings = settings
        self.base = (os.environ.get("KLING_API_BASE") or DEFAULT_BASE).strip().rstrip("/")
        host = urlparse(self.base).hostname or ""
        if urlparse(self.base).scheme != "https" or not (host == "klingai.com" or host.endswith(".klingai.com")):
            raise NotConfiguredError(f"KLING_API_BASE должен быть https://…klingai.com (сейчас: {self.base})")
        self.timeout = int(settings.get("generation.http_timeout_sec", 60)) if settings else 60

    # ------------------------------------------------------------ auth
    def configured(self) -> tuple[bool, str]:
        if secret("KLING_API_KEY"):
            return True, "API Key"
        if secret("KLING_ACCESS_KEY") and secret("KLING_SECRET_KEY"):
            return True, "Access Key + Secret Key (JWT)"
        return False, "Нет KLING_API_KEY (или пары KLING_ACCESS_KEY/KLING_SECRET_KEY) в .env"

    def _headers(self) -> dict[str, str]:
        key = secret("KLING_API_KEY")
        if key:
            token = key
        else:
            ak, sk = secret("KLING_ACCESS_KEY"), secret("KLING_SECRET_KEY")
            if not (ak and sk):
                raise NotConfiguredError(self.configured()[1])
            token = make_jwt(ak, sk)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _check(self, data: dict) -> dict:
        code = data.get("code", 0)
        if code not in (0, None):
            raise ProviderError(f"Kling code={code}: {data.get('message')}", code=code)
        return data

    @staticmethod
    def explain(err: Exception) -> str:
        """Человеческое пояснение к частым ошибкам авторизации/оплаты Kling (по таблице Error Codes)."""
        text = str(err)
        if "1002" in text or "api key not found" in text or "1000" in text or "1001" in text:
            return ("Kling не узнаёт ключ: в .env записан не сам ключ. Создайте новый на kling.ai/dev/api-key и "
                    "скопируйте КНОПКОЙ в окне сразу после создания (в таблице он показан со звёздочками). "
                    "Строка в .env: KLING_API_KEY=ключ — без пробелов и кавычек.")
        if "1102" in text:
            return "Пакет единиц API закончился или не куплен: kling.ai/dev → API Purchase → Video API."
        if "1103" in text:
            return "Нет доступа к модели/API для этого аккаунта — проверьте пакет Video API."
        return ""

    # ------------------------------------------------------------ pricing
    def billed_duration(self, req: VideoRequest) -> float:
        if req.kind == "avatar":
            return max(2.0, float(req.duration))
        allowed = MODEL_DURATIONS.get(req.model)
        if not allowed:
            return float(req.duration)
        for d in allowed:
            if d >= req.duration - 1e-6:
                return float(d)
        return float(allowed[-1])

    def estimate_usd(self, req: VideoRequest, pricing: dict) -> float:
        p = pricing.get("kling", {})
        unit = float(p.get("unit_usd", 0.14))
        models = p.get("models", {})
        secs = self.billed_duration(req)
        if req.kind == "avatar":
            rate = models.get("avatar", {}).get("per_second", {}).get(req.mode)
        else:
            key = req.model + ("-audio" if req.extra.get("audio") == "native" else "")
            rate = models.get(key, {}).get("per_second", {}).get(req.resolution)
        if rate is None:
            raise ProviderError(f"Нет тарифа для {req.kind}/{req.model}/{req.resolution or req.mode} в config/pricing.yaml")
        return round(rate * secs * unit, 4)

    # ------------------------------------------------------------ tasks
    def submit(self, req: VideoRequest) -> str:
        if req.kind == "image2video":
            return self._submit_i2v(req)
        if req.kind == "avatar":
            return self._submit_avatar(req)
        raise ProviderError(f"Kling: тип задачи {req.kind} не поддерживается адаптером")

    def _submit_i2v(self, req: VideoRequest) -> str:
        if not req.image:
            raise ProviderError("image2video требует изображение первого кадра")
        prompt = req.prompt
        if req.negative_prompt:
            # В API 2.6 нового стандарта нет отдельного negative_prompt — добавляем ограничения в текст.
            prompt = f"{prompt}\nAvoid: {req.negative_prompt}"
        contents: list[dict[str, Any]] = [
            {"type": "prompt", "text": prompt[:2500]},
            {"type": "first_frame", "url": b64_file(req.image, IMAGE_MAX_BYTES["image2video"])},
        ]
        if req.extra.get("last_frame"):
            contents.append({"type": "last_frame", "url": b64_file(Path(req.extra["last_frame"]), IMAGE_MAX_BYTES["image2video"])})
        body = {
            "contents": contents,
            "settings": {
                "audio": req.extra.get("audio", "off"),
                "resolution": req.resolution,
                "duration": int(self.billed_duration(req)),
            },
            "options": {"external_task_id": req.external_id, "watermark_info": {"enabled": False}},
        }
        data = self._check(http_json("POST", f"{self.base}/image-to-video/{req.model}", headers=self._headers(),
                                     json_body=body, timeout=self.timeout, safe_to_retry=False))
        task_id = (data.get("data") or {}).get("id")
        if not task_id:
            raise ProviderError(f"Kling не вернул id задачи: {str(data)[:300]}")
        return str(task_id)

    def _submit_avatar(self, req: VideoRequest) -> str:
        if not (req.image and req.audio):
            raise ProviderError("avatar требует изображение и аудио")
        mp3 = to_mp3(req.audio)
        try:
            sound = b64_file(mp3, AVATAR_MAX_AUDIO_BYTES)
        finally:
            mp3.unlink(missing_ok=True)
        body = {
            "image": b64_file(req.image, IMAGE_MAX_BYTES["avatar"]),
            "sound_file": sound,
            "prompt": req.prompt[:2500],
            "mode": req.mode,
            "external_task_id": req.external_id,
            "watermark_info": {"enabled": False},
        }
        data = self._check(http_json("POST", f"{self.base}/v1/videos/avatar/image2video", headers=self._headers(),
                                     json_body=body, timeout=self.timeout, safe_to_retry=False))
        task_id = (data.get("data") or {}).get("task_id")
        if not task_id:
            raise ProviderError(f"Kling не вернул task_id: {str(data)[:300]}")
        return str(task_id)

    def poll(self, task_id: str, kind: str) -> TaskState:
        if kind == "avatar":
            data = self._check(http_json("GET", f"{self.base}/v1/videos/avatar/image2video/{task_id}",
                                         headers=self._headers(), timeout=self.timeout))
            return self._parse_legacy(data.get("data") or {})
        data = self._check(http_json("GET", f"{self.base}/tasks", headers=self._headers(),
                                     params={"task_ids": task_id}, timeout=self.timeout))
        items = data.get("data") or []
        if not items:
            raise ProviderError(f"Kling: задача {task_id} не найдена")
        return self._parse_new(items[0])

    def find_by_external_id(self, external_id: str, kind: str) -> TaskState | None:
        try:
            if kind == "avatar":
                data = http_json("GET", f"{self.base}/v1/videos/avatar/image2video/{external_id}",
                                 headers=self._headers(), timeout=self.timeout)
                d = data.get("data") or {}
                return self._parse_legacy(d) if d.get("task_id") else None
            data = http_json("GET", f"{self.base}/tasks", headers=self._headers(),
                             params={"external_task_ids": external_id}, timeout=self.timeout)
            items = data.get("data") or []
            return self._parse_new(items[0]) if items else None
        except ProviderError:
            return None

    @staticmethod
    def _parse_new(item: dict) -> TaskState:
        status = normalize_status(item.get("status"))
        url = None
        for out in item.get("outputs") or []:
            if out.get("type") == "video" and out.get("url"):
                url = out["url"]
                break
        units = None
        for b in item.get("billing") or []:
            if b.get("charge_type") == "unit" and b.get("amount") is not None:
                units = (units or 0) + float(b["amount"])
        return TaskState(task_id=str(item.get("id")), status=status, video_url=url,
                         message=item.get("message") or "", billed_units=units, raw=item)

    @staticmethod
    def _parse_legacy(d: dict) -> TaskState:
        status = normalize_status(d.get("task_status"))
        url = None
        for v in (d.get("task_result") or {}).get("videos") or []:
            if v.get("url"):
                url = v["url"]
                break
        units = d.get("final_unit_deduction")
        return TaskState(task_id=str(d.get("task_id")), status=status, video_url=url,
                         message=d.get("task_status_msg") or "",
                         billed_units=float(units) if units not in (None, "") else None, raw=d)

    # ------------------------------------------------------------ account
    def account_info(self) -> dict[str, Any]:
        """Список resource packages и остатки (бесплатный метод, QPS ≤ 1; остаток с задержкой до 12 ч)."""
        end = int(time.time() * 1000)
        start = end - 365 * 24 * 3600 * 1000
        data = self._check(http_json("GET", f"{self.base}/account/costs", headers=self._headers(),
                                     params={"start_time": start, "end_time": end}, timeout=self.timeout, retries=1))
        packs = ((data.get("data") or {}).get("resource_pack_subscribe_infos")) or []
        return {
            "resource_packs": [
                {k: p.get(k) for k in ("resource_pack_name", "resource_pack_type", "total_quantity",
                                       "remaining_quantity", "status", "invalid_time")}
                for p in packs
            ],
            "remaining_units_online": sum(float(p.get("remaining_quantity") or 0) for p in packs if p.get("status") == "online"),
        }
