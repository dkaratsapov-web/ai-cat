"""Проект ролика (эпизод): папки, состояние, согласование сценария."""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Settings, get_settings
from .db import now_iso
from .models import Script, ScriptError

SUBDIRS = ("script", "images", "audio", "scenes", "subtitles", "output", "work", "publish", "imports")

# Жизненный цикл проекта
STATUSES = (
    "draft",         # сценарий в работе
    "approved",      # сценарий утверждён — можно запускать платные операции
    "voiced",        # озвучка готова
    "generated",     # все сцены получены
    "assembled",     # ролик смонтирован
    "qa_failed",
    "qa_passed",
    "final_approved",  # результат утверждён пользователем
    "packaged",      # пакет публикации готов
    "cancelled",
)


class ProjectError(RuntimeError):
    pass


def slugify(text: str) -> str:
    table = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
        "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
        "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    })
    s = text.lower().translate(table)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40] or "episode"


@dataclass
class Project:
    path: Path
    settings: Settings

    @property
    def id(self) -> str:
        return self.path.name

    # ---------- пути ----------
    def dir(self, name: str) -> Path:
        d = self.path / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def script_path(self) -> Path:
        return self.path / "script" / "script.yaml"

    @property
    def meta_path(self) -> Path:
        return self.path / "project.yaml"

    @property
    def final_video(self) -> Path:
        return self.dir("output") / f"{self.id}.mp4"

    def scene_clip(self, scene_id: str) -> Path:
        """Финальный нормализованный клип сцены (1080x1920, без звука, точной длины)."""
        return self.dir("work") / f"{scene_id}.norm.mp4"

    def scene_source(self, scene_id: str) -> Path | None:
        """Исходный (сгенерированный/импортированный) клип сцены, если есть."""
        d = self.dir("scenes")
        for name in (f"{scene_id}.lipsync.mp4", f"{scene_id}.mp4"):
            p = d / name
            if p.exists():
                return p
        return None

    def scene_audio(self, scene_id: str) -> Path:
        return self.dir("audio") / f"{scene_id}.wav"

    # ---------- метаданные ----------
    def meta(self) -> dict[str, Any]:
        if not self.meta_path.exists():
            return {}
        return yaml.safe_load(self.meta_path.read_text(encoding="utf-8")) or {}

    def save_meta(self, data: dict[str, Any]) -> None:
        data["updated_at"] = now_iso()
        self.meta_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    def update_meta(self, **kw: Any) -> dict[str, Any]:
        m = self.meta()
        m.update(kw)
        self.save_meta(m)
        return m

    @property
    def status(self) -> str:
        return self.meta().get("status", "draft")

    def set_status(self, status: str, **extra: Any) -> None:
        if status not in STATUSES:
            raise ProjectError(f"Неизвестный статус {status}")
        history = self.meta().get("history", [])
        history.append({"status": status, "ts": now_iso()})
        self.update_meta(status=status, history=history[-50:], **extra)

    # ---------- сценарий ----------
    def load_script(self) -> Script:
        if not self.script_path.exists():
            raise ProjectError(f"Нет сценария: {self.script_path}")
        return Script.load(self.script_path)

    def save_script(self, script: Script) -> None:
        script.save(self.script_path)

    def approve_script(self) -> Script:
        script = self.load_script()
        script.validate(self.settings.get("video.min_duration", 20), self.settings.get("video.max_duration", 35))
        self.set_status(
            "approved",
            approved_fingerprint=script.fingerprint(),
            approved_scenes={s.id: s.fingerprint() for s in script.scenes},
            approved_at=now_iso(),
        )
        return script

    def is_script_approved(self) -> bool:
        m = self.meta()
        if m.get("status") in ("draft", "cancelled") or not m.get("approved_fingerprint"):
            return False
        try:
            return self.load_script().fingerprint() == m["approved_fingerprint"]
        except (ScriptError, ProjectError):
            return False

    def require_approved(self) -> Script:
        if self.status == "cancelled":
            raise ProjectError("Производство отменено. Восстановите проект: studio script approve <id> после правок")
        if not self.is_script_approved():
            raise ProjectError(
                "Сценарий не утверждён или изменён после утверждения. "
                f"Проверьте раскадровку и выполните: studio script approve {self.id}"
            )
        return self.load_script()


def episodes_root(settings: Settings | None = None) -> Path:
    s = settings or get_settings()
    s.projects_dir.mkdir(parents=True, exist_ok=True)
    return s.projects_dir


def next_episode_id(title: str, settings: Settings | None = None) -> str:
    root = episodes_root(settings)
    nums = [int(m.group(1)) for p in root.iterdir() if (m := re.match(r"episode-(\d{3})", p.name))]
    n = (max(nums) + 1) if nums else 1
    return f"episode-{n:03d}-{slugify(title)}"


def create_project(script: Script, settings: Settings | None = None, episode_id: str | None = None) -> Project:
    s = settings or get_settings()
    eid = episode_id or next_episode_id(script.title, s)
    path = episodes_root(s) / eid
    if path.exists():
        raise ProjectError(f"Проект {eid} уже существует")
    for d in SUBDIRS:
        (path / d).mkdir(parents=True, exist_ok=True)
    script.id = eid
    proj = Project(path=path, settings=s)
    proj.save_script(script)
    proj.save_meta({"id": eid, "title": script.title, "status": "draft", "created_at": now_iso(), "history": []})
    return proj


def open_project(episode: str, settings: Settings | None = None) -> Project:
    s = settings or get_settings()
    root = episodes_root(s)
    p = root / episode
    if not p.exists():
        matches = sorted(x for x in root.iterdir() if x.is_dir() and x.name.startswith(episode))
        if len(matches) == 1:
            p = matches[0]
        elif not matches:
            raise ProjectError(f"Проект '{episode}' не найден в {root}")
        else:
            raise ProjectError(f"Неоднозначно: {', '.join(m.name for m in matches)}")
    return Project(path=p, settings=s)


def list_projects(settings: Settings | None = None) -> list[Project]:
    s = settings or get_settings()
    root = episodes_root(s)
    return [Project(path=p, settings=s) for p in sorted(root.iterdir()) if (p / "project.yaml").exists()]


def import_file(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst
