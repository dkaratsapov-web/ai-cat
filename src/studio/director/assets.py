"""Библиотека approved_assets: только из неё бриф может брать кадры и материалы."""
from __future__ import annotations

from pathlib import Path

from ..character.library import CharacterLibrary
from ..config import Settings


def approved_assets(settings: Settings) -> dict[str, dict]:
    """id → {kind, path, use, location, status}. Кадры кота — только утверждённые владельцем."""
    out: dict[str, dict] = {}
    lib = CharacterLibrary(settings)
    for r in lib.references():
        if r.get("status") != "approved":
            continue
        loc = "fitness_studio" if str(r["id"]).startswith("fit_") else "office"
        out[r["id"]] = {"kind": "character", "path": str(lib.file(r)), "use": r.get("use", ""),
                        "location": loc, "description": r.get("description", "")}
    base = settings.assets_dir
    for kind, folder, exts in (("logo", "logos", (".png",)), ("media", "templates/media", (".png", ".jpg")),
                               ("music", "music", (".mp3", ".wav"))):
        d = base / folder
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.suffix.lower() in exts:
                    out.setdefault(p.stem, {"kind": kind, "path": str(p), "use": "", "location": "", "description": ""})
    return out


def export_index(settings: Settings) -> Path:
    """assets/approved/index.yaml — утверждённые эталонные кадры кота с метаданными (id, путь, локация, одежда, ракурс,
    дата утверждения, теги). Claude берёт кадры отсюда в приоритете и не генерирует новый образ, если подходящий есть."""
    import yaml
    lib = CharacterLibrary(settings)
    rows = []
    for r in lib.references():
        if r.get("status") != "approved":
            continue
        loc = "fitness_studio" if str(r["id"]).startswith("fit_") else "office"
        rows.append({"id": r["id"], "filepath": str(Path("assets/character") / r["file"]), "location": loc,
                     "outfit": r.get("outfit") or "black hoodie with white paper-plane logo",
                     "angle": r.get("angle", ""), "approved_at": r.get("reviewed_at", ""),
                     "tags": [t for t in (r.get("use"), loc, r.get("kind")) if t], "description": r.get("description", "")})
    out = settings.assets_dir / "approved" / "index.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump({"assets": rows}, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
    return out


def pending_character_refs(settings: Settings) -> list[str]:
    return [r["id"] for r in CharacterLibrary(settings).references() if r.get("status") != "approved"]


def exists(path: str) -> bool:
    return Path(path).is_file()
