"""Модель сценария и раскадровки (хранится в редактируемом YAML)."""
from __future__ import annotations

import hashlib
import re
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONTENT_TYPES = {
    "expert": "Экспертный",
    "humor": "Юмористический",
    "case": "Кейс",
    "news": "Новостной",
    "commercial": "Коммерческий",
}

SCENE_TYPES = {
    "talking": "Говорящий персонаж",
    "character_motion": "Анимация персонажа без речи",
    "screen_demo": "Демонстрация рекламного кабинета",
    "screenshot": "Скриншот сайта",
    "infographic": "Инфографика / график",
    "ai_scene": "Дополнительная AI-сцена",
}

SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

GENERATORS = ("kling", "hedra", "runway", "local", "manual", "mock")
PAID_GENERATORS = ("kling", "hedra", "runway")


class ScriptError(ValueError):
    pass


@dataclass
class Scene:
    id: str
    type: str
    duration: float
    voiceover: str = ""
    visual: str = ""
    animation: str = ""
    generator: str = "local"
    # Ссылка на утверждённый референс персонажа (id из character.yaml)
    reference: str | None = None
    # Промпты для конкретных генераторов: {kling: "...", runway: "...", hedra: "..."}
    prompts: dict[str, str] = field(default_factory=dict)
    # Параметры локального рендера (карточка/график/скриншот/кенбернс)
    local: dict[str, Any] = field(default_factory=dict)
    # Нужна ли синхронизация губ (для talking — по умолчанию да)
    lipsync: bool | None = None
    # Текст субтитров, если отличается от озвучки (по умолчанию = voiceover)
    subtitle: str | None = None
    # Какие исходники нужны (скриншоты, данные и т.д.)
    assets: list[str] = field(default_factory=list)
    # Длительность AI-клипа (сек), если отличается от длительности сцены
    clip_duration: float | None = None
    notes: str = ""

    @property
    def needs_character(self) -> bool:
        if self.generator == "local":
            return self.local.get("kind") == "character"
        return self.type in ("talking", "character_motion", "ai_scene")

    @property
    def wants_lipsync(self) -> bool:
        if self.lipsync is not None:
            return bool(self.lipsync)
        return self.type == "talking"

    @property
    def subtitle_text(self) -> str:
        return (self.subtitle if self.subtitle is not None else self.voiceover).strip()

    def fingerprint(self) -> str:
        """Хеш содержимого сцены — используется для отслеживания изменений после утверждения."""
        d = asdict(self)
        return hashlib.sha256(json.dumps(d, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


@dataclass
class Script:
    id: str
    title: str
    content_type: str = "expert"
    topic: str = ""
    idea: str = ""
    audience: str = ""
    problem: str = ""
    format: str = ""
    target_duration: float = 30
    tone: str = ""
    hook: str = ""
    cta: str = ""
    voice_preset: str = "default"
    disclaimer: str | None = None
    scenes: list[Scene] = field(default_factory=list)
    publication: dict[str, Any] = field(default_factory=dict)

    # ------- сериализация -------
    @classmethod
    def from_dict(cls, d: dict) -> "Script":
        d = dict(d or {})
        scenes_raw = d.pop("scenes", []) or []
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(d) - known
        if unknown:
            raise ScriptError(f"Неизвестные поля сценария: {', '.join(sorted(unknown))}")
        scenes = []
        scene_fields = set(Scene.__dataclass_fields__)  # type: ignore[attr-defined]
        for i, s in enumerate(scenes_raw):
            s = dict(s)
            bad = set(s) - scene_fields
            if bad:
                raise ScriptError(f"Сцена #{i + 1}: неизвестные поля {', '.join(sorted(bad))}")
            if "id" not in s:
                s["id"] = f"s{i + 1:02d}"
            s["id"] = str(s["id"])
            if not SAFE_ID.match(s["id"]):
                raise ScriptError(f"Сцена #{i + 1}: недопустимый id '{s['id']}' — только латиница, цифры, '-' и '_'")
            s["prompts"] = s.get("prompts") or {}
            s["local"] = s.get("local") or {}
            s["assets"] = s.get("assets") or []
            scenes.append(Scene(**s))
        return cls(scenes=scenes, **d)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Компактнее: убираем пустые поля сцен
        clean = []
        for s in d["scenes"]:
            clean.append({k: v for k, v in s.items() if v not in (None, "", [], {}) or k in ("id", "type", "duration")})
        d["scenes"] = clean
        return d

    @classmethod
    def load(cls, path: Path) -> "Script":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False, width=110)
        Path(path).write_text(text, encoding="utf-8")

    # ------- вычисляемое -------
    @property
    def planned_duration(self) -> float:
        return round(sum(s.duration for s in self.scenes), 2)

    @property
    def full_voiceover(self) -> str:
        return " ".join(s.voiceover.strip() for s in self.scenes if s.voiceover.strip())

    def scene(self, scene_id: str) -> Scene:
        for s in self.scenes:
            if s.id == scene_id:
                return s
        raise ScriptError(f"Сцена {scene_id} не найдена. Доступны: {', '.join(s.id for s in self.scenes)}")

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    # ------- проверка -------
    def validate(self, min_duration: float = 20, max_duration: float = 35) -> list[str]:
        """Возвращает список предупреждений; бросает ScriptError при критических ошибках."""
        errors: list[str] = []
        warnings: list[str] = []
        if self.content_type not in CONTENT_TYPES:
            errors.append(f"content_type должен быть одним из: {', '.join(CONTENT_TYPES)}")
        if not self.scenes:
            errors.append("В сценарии нет сцен")
        ids = [s.id for s in self.scenes]
        if len(ids) != len(set(ids)):
            errors.append("Повторяющиеся id сцен")
        for s in self.scenes:
            if not SAFE_ID.match(s.id):
                errors.append(f"id сцены '{s.id}' — только латиница, цифры, '-' и '_' (до 32 символов)")
            if s.type not in SCENE_TYPES:
                errors.append(f"{s.id}: неизвестный тип сцены '{s.type}' (допустимо: {', '.join(SCENE_TYPES)})")
            if s.generator not in GENERATORS:
                errors.append(f"{s.id}: неизвестный генератор '{s.generator}'")
            if s.duration <= 0:
                errors.append(f"{s.id}: длительность должна быть > 0")
            if s.generator in PAID_GENERATORS and not (s.prompts.get(s.generator) or s.visual):
                errors.append(f"{s.id}: для генератора {s.generator} нужен промпт (prompts.{s.generator}) или visual")
            for hl in s.local.get("highlights", []) or []:
                extra = set(hl) - {"box", "at", "label", "label_pos"}
                if extra:
                    errors.append(f"{s.id}: в рамке лишние поля {sorted(extra)} — возьмите label в кавычки, если в нём есть запятая")
            if s.generator == "local" and not s.local.get("kind"):
                errors.append(f"{s.id}: для локальной сцены нужен local.kind (card | chart | screenshot | character | image)")
            if s.voiceover:
                words = len(s.voiceover.split())
                # Русская речь ~2.3–2.8 слова/сек
                if words / max(s.duration, 0.1) > 3.2:
                    warnings.append(f"{s.id}: {words} слов на {s.duration}с — текст может не уместиться")
        total = self.planned_duration
        if total < min_duration or total > max_duration:
            warnings.append(f"Плановая длительность {total}с вне диапазона {min_duration}–{max_duration}с")
        if self.scenes and self.scenes[0].duration > 4:
            warnings.append("Первая сцена длиннее 4с — хук должен срабатывать за 2–3 секунды")
        if errors:
            raise ScriptError("; ".join(errors))
        return warnings
