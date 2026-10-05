"""Адаптер Higgsfield API (агрегатор моделей: Seedance, Kling, Wan и собственные модели Higgsfield).

Протокол проверен по официальным SDK (PyPI higgsfield-client 0.2.0, github.com/higgsfield-ai/higgsfield-js):
  Авторизация: Authorization: Key <API_KEY>:<API_SECRET>
  POST https://api.higgsfield.ai/<модель>  (JSON-аргументы)      -> {request_id, status_url, cancel_url}
  GET  /requests/<id>/status  -> {status: queued|in_progress|completed|failed|nsfw|canceled, video: {url}}
  POST /files/generate-upload-url {content_type} -> {public_url, upload_url, upload_headers}; затем PUT байтов
По failed и nsfw деньги возвращаются (README JS SDK).

Аргументы моделей взяты со страниц open.higgsfield.ai (через поиск) — перед первой платной генерацией
сверьте их в кабинете. Seedance: image_url, prompt, duration (целые секунды), resolution, generate_audio.
Говорящее видео (Speak) — экспериментально: неизвестно, работает ли на коте.
"""
from __future__ import annotations

import math
import mimetypes
from pathlib import Path
from typing import Any

import requests

from ..config import secret
from .base import NotConfiguredError, ProviderError, TaskState, VideoProvider, VideoRequest, http_json

BASE = "https://api.higgsfield.ai"
STATUS_MAP = {"queued": "submitted", "in_progress": "processing", "completed": "succeeded",
              "failed": "failed", "nsfw": "failed", "canceled": "failed", "cancelled": "failed"}


def credentials() -> str:
    key, sec = secret("HIGGSFIELD_API_KEY"), secret("HIGGSFIELD_API_SECRET")
    if key and sec:
        return f"{key}:{sec}"
    combined = secret("HF_KEY")   # формат официального SDK: "ключ:секрет"
    return combined or ""


class HiggsfieldProvider(VideoProvider):
    name = "higgsfield"
    kinds = ("image2video", "avatar")

    def __init__(self, settings=None):
        self.settings = settings

    def configured(self) -> tuple[bool, str]:
        if credentials():
            return True, "API Key + Secret"
        return False, "Нет HIGGSFIELD_API_KEY и HIGGSFIELD_API_SECRET в .env"

    def _headers(self) -> dict[str, str]:
        cred = credentials()
        if not cred:
            raise NotConfiguredError("Нет HIGGSFIELD_API_KEY / HIGGSFIELD_API_SECRET")
        return {"Authorization": f"Key {cred}", "Content-Type": "application/json"}

    # ---------- деньги ----------
    def billed_duration(self, req: VideoRequest) -> float:
        if req.kind == "avatar":   # Speak: 5 / 10 / 15 с
            for d in (5, 10, 15):
                if d >= req.duration - 1e-6:
                    return float(d)
            return 15.0
        lo, hi = req.extra.get("min_duration", 4), req.extra.get("max_duration", 30)
        return float(min(max(math.ceil(req.duration - 1e-6), lo), hi))

    def estimate_usd(self, req: VideoRequest, pricing: dict) -> float:
        model = (pricing.get("higgsfield", {}).get("models", {}) or {}).get(req.model) or {}
        rates = model.get("usd_per_second", {})
        rate = rates.get(req.resolution) if isinstance(rates, dict) else rates
        if rate is None:
            raise ProviderError(f"Нет тарифа Higgsfield для {req.model}/{req.resolution} в config/pricing.yaml — "
                                "внесите цену из кабинета, иначе смета невозможна")
        return round(float(rate) * self.billed_duration(req), 4)

    # ---------- файлы ----------
    def _upload(self, path: Path) -> str:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        info = http_json("POST", f"{BASE}/files/generate-upload-url", headers=self._headers(),
                         json_body={"content_type": ctype}, timeout=60)
        if not info.get("upload_url") or not info.get("public_url"):
            raise ProviderError(f"Higgsfield: не выдал адрес загрузки: {str(info)[:300]}")
        headers = info.get("upload_headers") or {"Content-Type": ctype}
        try:
            r = requests.put(info["upload_url"], data=path.read_bytes(), headers=headers, timeout=180,
                             allow_redirects=False)
        except requests.RequestException as e:
            raise ProviderError(f"Higgsfield: загрузка {path.name} не удалась: {e}", retryable=True) from e
        if r.status_code >= 400:
            raise ProviderError(f"Higgsfield upload HTTP {r.status_code}: {r.text[:300]}", status=r.status_code)
        return info["public_url"]

    # ---------- задачи ----------
    def submit(self, req: VideoRequest) -> str:
        if not req.image:
            raise ProviderError("Higgsfield: нужен кадр-референс")
        image_url = self._upload(req.image)
        if req.kind == "avatar":
            if not req.audio:
                raise ProviderError("Higgsfield Speak: нужна озвучка (WAV)")
            audio_url = self._upload(req.audio)
            body: dict[str, Any] = {
                "input_image": {"type": "image_url", "image_url": image_url},
                "input_audio": {"type": "audio_url", "audio_url": audio_url},
                "prompt": req.prompt or "talking to camera",
                "quality": req.mode if req.mode in ("mid", "high") else "mid",
                "duration": int(self.billed_duration(req)),
            }
        elif req.kind == "image2video":
            body = {
                "image_url": image_url,
                "prompt": req.prompt,
                "duration": int(self.billed_duration(req)),
                "resolution": req.resolution,
                "generate_audio": False,      # озвучка своя; со звуком генерация дороже
                "output_format": "mp4",
            }
        else:
            raise ProviderError(f"Higgsfield: тип задачи {req.kind} не поддерживается")
        path = req.model.lstrip("/")
        # Создание задачи — платное: при обрыве связи НЕ повторяем (AmbiguousSubmitError → ручная проверка)
        data = http_json("POST", f"{BASE}/{path}", headers=self._headers(), json_body=body, timeout=90,
                         safe_to_retry=False)
        rid = data.get("request_id")
        if not rid:
            raise ProviderError(f"Higgsfield не вернул request_id: {str(data)[:300]}")
        return str(rid)

    def poll(self, task_id: str, kind: str, context: dict | None = None) -> TaskState:
        st = http_json("GET", f"{BASE}/requests/{task_id}/status", headers=self._headers())
        raw = str(st.get("status", "")).lower()
        status = STATUS_MAP.get(raw, "processing")
        url = (st.get("video") or {}).get("url") if isinstance(st.get("video"), dict) else None
        msg = ""
        if raw == "nsfw":
            msg = "Higgsfield: отклонено модерацией (деньги возвращаются)"
        elif status == "failed":
            msg = f"Higgsfield: {raw}; {str(st.get('error') or st.get('detail') or '')[:300]}"
        elif status == "succeeded" and not url:
            status, msg = "failed", f"Higgsfield: нет ссылки на видео в ответе: {str(st)[:300]}"
        return TaskState(task_id=task_id, status=status, video_url=url, message=msg, raw=st)

    def find_by_external_id(self, external_id: str, kind: str) -> TaskState | None:
        return None   # у API нет поиска по нашему id: неясную отправку проверяем вручную в кабинете
