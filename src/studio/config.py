"""Конфигурация, пути и работа с секретами."""
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def find_root(start: Path | None = None) -> Path:
    """Корень проекта: ближайший каталог с config/studio.yaml (или STUDIO_ROOT)."""
    env = os.environ.get("STUDIO_ROOT")
    if env:
        return Path(env).resolve()
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "config" / "studio.yaml").exists():
            return cand
    # запасной вариант — расположение пакета (src/studio -> корень)
    return Path(__file__).resolve().parents[2]


def load_dotenv(path: Path) -> None:
    """Минимальный парсер .env без внешних зависимостей. Не перезаписывает уже заданные переменные."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():  # -sig: Блокнот Windows может добавить BOM
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


SECRET_ENV_NAMES = (
    "KLING_API_KEY",
    "KLING_ACCESS_KEY",
    "KLING_SECRET_KEY",
    "ELEVENLABS_API_KEY",
    "YANDEX_API_KEY",
    "YANDEX_IAM_TOKEN",
    "HEDRA_API_KEY",
    "RUNWAYML_API_SECRET",
    "ANTHROPIC_API_KEY",
)


def redact(text: str) -> str:
    """Удаляет значения известных секретов и токены авторизации из текста (логи, ошибки)."""
    if not text:
        return text
    out = str(text)
    for name in SECRET_ENV_NAMES:
        raw = os.environ.get(name) or ""
        for val in {raw, raw.strip(), repr(raw)[1:-1]}:
            if val and len(val.strip()) >= 6:
                out = out.replace(val, f"<{name}>")
    out = re.sub(r"(Api-Key\s+)[A-Za-z0-9._\-]+", r"\1<redacted>", out)
    out = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1<redacted>", out)
    out = re.sub(r"(?i)(xi-api-key|x-api-key|authorization)([\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+", r"\1\2<redacted>", out)
    return out


def secret(name: str) -> str | None:
    val = (os.environ.get(name) or "").strip()  # перевод строки в ключе ломает заголовок и утекает в ошибку
    return val or None


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


@dataclass
class Settings:
    root: Path
    data: dict[str, Any] = field(default_factory=dict)

    # --- пути ---
    @property
    def projects_dir(self) -> Path:
        return self.root / "projects"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def character_dir(self) -> Path:
        return self.assets_dir / "character"

    @property
    def fonts_dir(self) -> Path:
        return self.assets_dir / "fonts"

    @property
    def data_dir(self) -> Path:
        d = self.root / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.sqlite3"

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def load_yaml(self, rel: str) -> dict:
        p = self.root / rel
        if not p.exists():
            return {}
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


_settings: Settings | None = None


def get_settings(root: Path | None = None, reload: bool = False) -> Settings:
    global _settings
    if _settings is not None and not reload and root is None:
        return _settings
    r = root or find_root()
    load_dotenv(r / ".env")
    data = {}
    cfg = r / "config" / "studio.yaml"
    if cfg.exists():
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    local = r / "config" / "studio.local.yaml"
    if local.exists():
        data = deep_merge(data, yaml.safe_load(local.read_text(encoding="utf-8")) or {})
    _settings = Settings(root=r, data=data)
    return _settings
