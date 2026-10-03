"""Бесплатный «генератор» для тестового прогона конвейера без платных API.

Создаёт клип из референсного изображения (плавный зум + плашка MOCK), имитируя асинхронную задачу.
"""
from __future__ import annotations

import json
import uuid
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from pathlib import Path

from PIL import Image

from ..editing.local_scenes import kenburns_frames, write_frames
from ..models import Scene
from .base import TaskState, VideoProvider, VideoRequest


class MockProvider(VideoProvider):
    name = "mock"
    paid = False
    kinds = ("image2video", "avatar")

    def __init__(self, settings=None):
        self.settings = settings
        self.dir = (settings.data_dir if settings else Path("/tmp")) / "mock_tasks"
        self.dir.mkdir(parents=True, exist_ok=True)

    def configured(self) -> tuple[bool, str]:
        return True, "локальная заглушка"

    def billed_duration(self, req: VideoRequest) -> float:
        return float(req.duration)

    def estimate_usd(self, req: VideoRequest, pricing: dict) -> float:
        return 0.0

    def submit(self, req: VideoRequest) -> str:
        task_id = f"mock-{uuid.uuid4().hex[:10]}"
        out = self.dir / f"{task_id}.mp4"
        scene = Scene(id=task_id, type="ai_scene", duration=req.duration, local={"zoom_to": 1.12})
        label = f"MOCK {req.kind}"
        write_frames(out, kenburns_frames(Image.open(req.image), scene, self.settings, watermark=label),
                     req.duration, self.settings)
        (self.dir / f"{task_id}.json").write_text(json.dumps({"external_id": req.external_id, "kind": req.kind}))
        return task_id

    def poll(self, task_id: str, kind: str) -> TaskState:
        out = self.dir / f"{task_id}.mp4"
        if out.exists():
            return TaskState(task_id=task_id, status="succeeded", video_url=out.as_uri())  # file:///… — переносимо между ОС
        return TaskState(task_id=task_id, status="failed", message="mock: файл не найден")

    def find_by_external_id(self, external_id: str, kind: str) -> TaskState | None:
        for meta in self.dir.glob("*.json"):
            if json.loads(meta.read_text()).get("external_id") == external_id:
                return self.poll(meta.stem, kind)
        return None

    def download(self, url: str, dest: Path, timeout: int = 300) -> Path:
        # Корректно и для Windows (file:///C:/…), и для Linux (file:///home/…)
        parsed = urlparse(url)
        src = Path(url2pathname(unquote(parsed.path))) if parsed.scheme == "file" else Path(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return dest
