"""Адаптер Hedra API v3 (говорящий персонаж по фото + аудио). ЭКСПЕРИМЕНТАЛЬНЫЙ.

Источник: официальный SDK github.com/hedra-labs/hedra-python (база https://api.hedra.com/v3).
  POST /files (multipart 'file') -> {url}; внешние URL не принимаются, только загруженные
  POST /models/hedra-character-3 {"input": {...}} + заголовок Idempotency-Key -> {job_id}
  GET  /jobs/{id}/status -> IN_QUEUE | IN_PROGRESS | COMPLETED | FAILED
  GET  /jobs/{id} -> outputs[].url
Авторизация: Authorization: Bearer <key_id>:<secret>.

Если API недоступен на вашем тарифе — используйте ручной импорт:
  studio scene import <episode> <scene> путь/к/ролику_из_hedra.mp4
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from ..config import secret
from .base import AmbiguousSubmitError, NotConfiguredError, ProviderError, TaskState, VideoProvider, VideoRequest, http_json

BASE = "https://api.hedra.com/v3"
STATUS_MAP = {"IN_QUEUE": "submitted", "IN_PROGRESS": "processing", "COMPLETED": "succeeded", "FAILED": "failed"}


class HedraProvider(VideoProvider):
    name = "hedra"
    kinds = ("avatar",)

    def __init__(self, settings=None):
        self.settings = settings

    def configured(self) -> tuple[bool, str]:
        return (True, "API Key") if secret("HEDRA_API_KEY") else (False, "Нет HEDRA_API_KEY в .env")

    def _headers(self) -> dict[str, str]:
        key = secret("HEDRA_API_KEY")
        if not key:
            raise NotConfiguredError("Нет HEDRA_API_KEY")
        return {"Authorization": f"Bearer {key}"}

    def billed_duration(self, req: VideoRequest) -> float:
        return float(req.duration)

    def estimate_usd(self, req: VideoRequest, pricing: dict) -> float:
        rates = pricing.get("hedra", {}).get("models", {}).get(req.model, {}).get("usd_per_second", {})
        rate = rates.get(req.resolution)
        if rate is None:
            raise ProviderError(f"Нет тарифа Hedra для {req.model}/{req.resolution}")
        return round(rate * self.billed_duration(req), 4)

    def _upload(self, path: Path) -> str:
        try:
            with open(path, "rb") as f:
                r = requests.post(f"{BASE}/files", headers=self._headers(), files={"file": (path.name, f)}, timeout=120)
        except requests.RequestException as e:
            raise ProviderError(f"Hedra: загрузка {path.name} не удалась: {e}", retryable=True) from e
        if r.status_code >= 400:
            raise ProviderError(f"Hedra upload HTTP {r.status_code}: {r.text[:300]}", status=r.status_code)
        return r.json()["url"]

    def submit(self, req: VideoRequest) -> str:
        if req.kind != "avatar" or not (req.image and req.audio):
            raise ProviderError("Hedra: поддерживается только avatar (изображение + аудио)")
        img_url = self._upload(req.image)
        audio_url = self._upload(req.audio)
        body = {
            "input": {
                "prompt": req.prompt or "talking to camera",
                "aspect_ratio": "9:16",
                "resolution": req.resolution,
                "duration_ms": int(req.duration * 1000),
                "start_image": {"source": "url", "url": img_url},
                "audio": {"source": "url", "url": audio_url},
            }
        }
        headers = {**self._headers(), "Idempotency-Key": req.external_id, "Content-Type": "application/json"}
        try:
            data = http_json("POST", f"{BASE}/models/{req.model}", headers=headers, json_body=body,
                             timeout=60, safe_to_retry=True)  # Idempotency-Key делает повтор безопасным
        except AmbiguousSubmitError:
            raise
        job_id = data.get("job_id")
        if not job_id:
            raise ProviderError(f"Hedra не вернул job_id: {str(data)[:300]}")
        return str(job_id)

    def poll(self, task_id: str, kind: str) -> TaskState:
        st = http_json("GET", f"{BASE}/jobs/{task_id}/status", headers=self._headers())
        status = STATUS_MAP.get(st.get("status", ""), "processing")
        url = None
        msg = ""
        if status == "succeeded":
            job = http_json("GET", f"{BASE}/jobs/{task_id}", headers=self._headers())
            for out in job.get("outputs") or []:
                if out.get("url"):
                    url = out["url"]
                    break
        elif status == "failed":
            msg = str(st.get("logs") or "")[:300]
        return TaskState(task_id=task_id, status=status, video_url=url, message=msg, raw=st)

    def account_info(self) -> dict[str, Any]:
        return http_json("GET", f"{BASE}/balance", headers=self._headers(), retries=1)
