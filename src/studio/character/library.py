"""Библиотека персонажа: референсы, ручное утверждение, «якорь» внешности для промптов."""
from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageOps

from ..config import Settings
from ..db import now_iso


class CharacterError(RuntimeError):
    pass


@dataclass
class CharacterLibrary:
    settings: Settings

    @property
    def dir(self) -> Path:
        return self.settings.character_dir

    @property
    def path(self) -> Path:
        return self.dir / "character.yaml"

    def data(self) -> dict[str, Any]:
        if not self.path.exists():
            raise CharacterError(f"Нет {self.path}")
        return yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}

    def save(self, data: dict[str, Any]) -> None:
        self.path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")

    # ---------- референсы ----------
    def references(self, status: str | None = None) -> list[dict]:
        refs = self.data().get("references", []) or []
        return [r for r in refs if status is None or r.get("status") == status]

    def get(self, ref_id: str) -> dict:
        for r in self.references():
            if r["id"] == ref_id:
                return r
        raise CharacterError(f"Референс '{ref_id}' не найден")

    def file(self, ref: dict) -> Path:
        return self.dir / ref["file"]

    def add(self, src: Path, ref_id: str, *, kind: str = "reference", angle: str = "", description: str = "",
            prompt: str | None = None, model: str | None = None, params: dict | None = None) -> dict:
        if not re.match(r"^[A-Za-z0-9_-]{1,64}$", ref_id):
            raise CharacterError("id референса — только латиница, цифры, '-' и '_'")
        src = Path(src)
        if not src.exists():
            raise CharacterError(f"Файл не найден: {src}")
        data = self.data()
        if any(r["id"] == ref_id for r in data.get("references", [])):
            raise CharacterError(f"Референс '{ref_id}' уже есть")
        ref_dir = self.dir / "references"
        ref_dir.mkdir(parents=True, exist_ok=True)
        # Храним исходник и JPEG-копию для API (jpg/png поддерживаются Kling)
        source = ref_dir / f"{ref_id}{src.suffix.lower()}"
        shutil.copy2(src, source)
        target = ref_dir / f"{ref_id}.jpg"
        if source != target:
            ImageOps.exif_transpose(Image.open(source)).convert("RGB").save(target, quality=95)
        ref = {
            "id": ref_id, "file": f"references/{target.name}", "source_file": f"references/{source.name}",
            "kind": kind, "angle": angle, "description": description, "status": "pending",
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()[:16], "added_at": now_iso(),
            "generation": {"prompt": prompt, "model": model, "params": params or {}},
        }
        data.setdefault("references", []).append(ref)
        self.save(data)
        return ref

    def set_status(self, ref_id: str, status: str, note: str = "") -> dict:
        if status not in ("approved", "rejected", "pending"):
            raise CharacterError("Статус: approved | rejected | pending")
        data = self.data()
        for r in data.get("references", []):
            if r["id"] == ref_id:
                r["status"] = status
                r["reviewed_at"] = now_iso()
                if note:
                    r["review_note"] = note
                self.save(data)
                return r
        raise CharacterError(f"Референс '{ref_id}' не найден")

    def resolve(self, ref_id: str | None) -> tuple[dict, Path]:
        """Утверждённый референс по id; без id — первый утверждённый."""
        approved = self.references("approved")
        if not approved:
            raise CharacterError(
                "Нет утверждённых референсов персонажа. Просмотрите assets/character/references и выполните "
                "studio character approve <id>"
            )
        if ref_id:
            ref = self.get(ref_id)
            if ref.get("status") != "approved":
                raise CharacterError(f"Референс '{ref_id}' не утверждён (status={ref.get('status')})")
        else:
            ref = approved[0]
        p = self.file(ref)
        if not p.exists():
            raise CharacterError(f"Файл референса отсутствует: {p}")
        return ref, p

    # ---------- промпты ----------
    def anchor(self, outfit: str | None = None, location: str | None = None) -> str:
        """Неизменяемое описание внешности, добавляемое в каждый промпт с персонажем."""
        d = self.data()
        traits = "; ".join(d.get("identity_traits", []))
        parts = [
            f"Character: {traits}.",
            f"Outfit: {outfit or d.get('default_outfit', '')}.",
            f"Setting: {location or d.get('default_location', '')}.",
            f"Style: {d.get('style', '')}.",
            "Keep the cat's face, fur pattern, eye shape and folded ears exactly as in the reference image.",
        ]
        return " ".join(p for p in parts if p.split(":", 1)[-1].strip(" ."))

    def negative(self) -> str:
        return (self.data().get("negative_prompt") or "").strip()

    def build_prompt(self, scene_prompt: str, *, outfit: str | None = None, location: str | None = None) -> str:
        return f"{scene_prompt.strip()}\n\n{self.anchor(outfit, location)}"

    # ---------- подготовка кадра ----------
    def vertical_frame(self, src: Path, dest: Path, focus: tuple[float, float] = (0.6, 0.45),
                       min_height: int = 1280) -> Path:
        """Кадрирует референс в 9:16 вокруг точки фокуса (по умолчанию — мордочка кота на основном фото).

        Видеогенераторы берут соотношение сторон из первого кадра, поэтому для Reels нужен вертикальный кадр.
        Лучше — заранее утверждённые вертикальные референсы; это автоматический запасной вариант.
        """
        img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
        target_ratio = 9 / 16
        if img.width / img.height > target_ratio:
            cw, ch = int(img.height * target_ratio), img.height
        else:
            cw, ch = img.width, int(img.width / target_ratio)
        cx = min(max(int(img.width * focus[0]) - cw // 2, 0), img.width - cw)
        cy = min(max(int(img.height * focus[1]) - ch // 2, 0), img.height - ch)
        crop = img.crop((cx, cy, cx + cw, cy + ch))
        if crop.height < min_height:
            crop = crop.resize((int(min_height * target_ratio), min_height), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest, quality=95)
        return dest
