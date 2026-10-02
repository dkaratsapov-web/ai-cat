"""Адаптер Runway API (сложные кинематографичные сцены). ОПЦИОНАЛЬНЫЙ, не нужен для MVP.

Источник: официальный SDK github.com/runwayml/sdk-python.
  База: https://api.dev.runwayml.com, заголовки Authorization: Bearer <key>, X-Runway-Version: 2024-11-06
  POST /v1/image_to_video {model, promptImage, promptText, ratio, duration}
  GET  /v1/tasks/{id} -> PENDING | THROTTLED | RUNNING | SUCCEEDED | FAILED | CANCELLED; output[] — URL (живут 24–48 ч)
  GET  /v1/organization -> creditBalance (бесплатно)
Вертикальный формат для gen4_turbo / gen4.5: ratio "720:1280".
"""
from __future__ import annotations

import base64
import mimetypes
from typing import Any

from ..config import secret
from .base import NotConfiguredError, ProviderError, TaskState, VideoProvider, VideoRequest, http_json

BASE = "https://api.dev.runwayml.com"
VERSION = "2024-11-06"
STATUS_MAP = {"PENDING": "submitted", "THROTTLED": "submitted", "RUNNING": "processing",
              "SUCCEEDED": "succeeded", "FAILED": "failed", "CANCELLED": "failed"}
DATA_URI_LIMIT = 5 * 1024 * 1024


class RunwayProvider(VideoProvider):
    name = "runway"
    kinds = ("image2video",)

    def __init__(self, settings=None):
        self.settings = settings

    def configured(self) -> tuple[bool, str]:
        return (True, "API Key") if secret("RUNWAYML_API_SECRET") else (False, "Нет RUNWAYML_API_SECRET в .env")

    def _headers(self) -> dict[str, str]:
        key = secret("RUNWAYML_API_SECRET")
        if not key:
            raise NotConfiguredError("Нет RUNWAYML_API_SECRET")
        return {"Authorization": f"Bearer {key}", "X-Runway-Version": VERSION, "Content-Type": "application/json"}

    def billed_duration(self, req: VideoRequest) -> float:
        return float(max(2, min(10, round(req.duration))))

    def estimate_usd(self, req: VideoRequest, pricing: dict) -> float:
        p = pricing.get("runway", {})
        cps = p.get("models", {}).get(req.model, {}).get("credits_per_second")
        if cps is None:
            raise ProviderError(f"Нет тарифа Runway для {req.model}")
        return round(cps * self.billed_duration(req) * float(p.get("credit_usd", 0.01)), 4)

    def submit(self, req: VideoRequest) -> str:
        if not req.image:
            raise ProviderError("Runway image_to_video требует изображение")
        raw = req.image.read_bytes()
        if len(raw) > DATA_URI_LIMIT:
            raise ProviderError("Изображение больше 5 МБ — уменьшите его для Runway")
        mime = mimetypes.guess_type(req.image.name)[0] or "image/jpeg"
        body = {
            "model": req.model,
            "promptImage": f"data:{mime};base64,{base64.b64encode(raw).decode()}",
            "promptText": req.prompt[:1000],
            "ratio": req.extra.get("ratio", "720:1280"),
            "duration": int(self.billed_duration(req)),
        }
        data = http_json("POST", f"{BASE}/v1/image_to_video", headers=self._headers(), json_body=body,
                         safe_to_retry=False)
        if not data.get("id"):
            raise ProviderError(f"Runway не вернул id: {str(data)[:300]}")
        return str(data["id"])

    def poll(self, task_id: str, kind: str) -> TaskState:
        d = http_json("GET", f"{BASE}/v1/tasks/{task_id}", headers=self._headers())
        status = STATUS_MAP.get(d.get("status", ""), "processing")
        out = d.get("output") or []
        return TaskState(task_id=task_id, status=status, video_url=out[0] if out else None,
                         message=str(d.get("failure") or ""), raw=d)

    def account_info(self) -> dict[str, Any]:
        d = http_json("GET", f"{BASE}/v1/organization", headers=self._headers(), retries=1)
        return {"creditBalance": d.get("creditBalance")}
