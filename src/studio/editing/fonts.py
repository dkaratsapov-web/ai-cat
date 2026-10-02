"""Поиск шрифтов с кириллицей для Pillow и libass."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

SYSTEM_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
]
SYSTEM_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]


def project_fonts(fonts_dir: Path) -> list[Path]:
    if not fonts_dir.exists():
        return []
    return sorted([*fonts_dir.glob("*.ttf"), *fonts_dir.glob("*.otf")])


def font_path(fonts_dir: Path, bold: bool = True) -> str:
    own = project_fonts(fonts_dir)
    if own:
        pick = [p for p in own if ("bold" in p.name.lower()) == bold] or own
        return str(pick[0])
    for cand in SYSTEM_BOLD if bold else SYSTEM_REGULAR:
        if Path(cand).exists():
            return cand
    raise FileNotFoundError("Не найден TTF-шрифт с кириллицей. Положите шрифт в assets/fonts/")


@lru_cache(maxsize=64)
def _load(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def font(fonts_dir: Path, size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    return _load(font_path(fonts_dir, bold), size)


def ass_font_name(fonts_dir: Path, preferred: str, fallback: str) -> str:
    """Имя гарнитуры для libass: из шрифта в assets/fonts, иначе fallback (системный)."""
    own = sorted(project_fonts(fonts_dir), key=lambda p: "bold" not in p.name.lower())
    if own:
        try:
            f = ImageFont.truetype(str(own[0]), 20)
            family, _style = f.getname()
            if family:
                return family
        except OSError:
            pass
        return preferred
    return fallback
