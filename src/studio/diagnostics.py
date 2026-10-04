"""Диагностика окружения (`studio doctor`). Ключи никогда не выводятся — только «задан / не задан»."""
from __future__ import annotations

import os
import sys

from .character.library import CharacterError, CharacterLibrary
from .config import DOTENV_KEYS, Settings, redact
from .editing import ffmpeg
from .editing.fonts import font_path, project_fonts
from .integrations import tts_provider, video_provider

REQUIRED_FILTERS = ("ass", "xfade", "loudnorm", "blackdetect", "silencedetect", "sidechaincompress", "tpad")


# Публичные префиксы ключей (видны в кабинетах) — помогают сверить, тот ли ключ вписан
KNOWN_PREFIXES = {"KLING_API_KEY": "api-key-kling-"}


def key_shape(val: str | None) -> str:
    """Форма ключа без раскрытия значения: длина и подозрительные символы."""
    if not val:
        return "—"
    problems = []
    if "*" in val or "•" in val or "…" in val:
        problems.append("звёздочки/точки — скопирована СКРЫТАЯ версия из таблицы")
    if val != val.strip():
        problems.append("пробел или перевод строки по краям")
    if " " in val.strip():
        problems.append("пробел внутри")
    if any(c in val for c in "\"'«»"):
        problems.append("кавычки")
    if any("а" <= c.lower() <= "я" or c.lower() == "ё" for c in val):
        problems.append("русские буквы")
    if not val.isascii():
        problems.append("не-латинские символы")
    note = f"задан, длина {len(val.strip())}"
    return note + ("  ⚠ " + "; ".join(problems) if problems else "")


def run_doctor(settings: Settings, check_api: bool = False) -> bool:
    ok = True

    def line(status: str, name: str, detail: str = "") -> None:
        nonlocal ok
        if status == "FAIL":
            ok = False
        print(f"[{status:4}] {name}{': ' + detail if detail else ''}")

    line("OK" if sys.version_info >= (3, 10) else "FAIL", "Python", sys.version.split()[0] + " (нужно ≥ 3.10)")
    for tool in ("ffmpeg", "ffprobe"):
        p = ffmpeg.which(tool)
        line("OK" if p else "FAIL", tool, p or "не найден — установите FFmpeg и добавьте в PATH")
    if ffmpeg.which("ffmpeg"):
        missing = ffmpeg.missing_filters(REQUIRED_FILTERS)
        line("OK" if not missing else "FAIL", "Фильтры FFmpeg",
             "все на месте" if not missing else f"нет: {', '.join(missing)} (нужна сборка с libass)")
    try:
        own = project_fonts(settings.fonts_dir)
        line("OK" if own else "WARN", "Шрифт", font_path(settings.fonts_dir) +
             ("" if own else " — системный; для фирменного стиля положите TTF в assets/fonts/"))
    except FileNotFoundError as e:
        line("FAIL", "Шрифт", str(e))

    try:
        lib = CharacterLibrary(settings)
        refs = lib.references()
        appr = lib.references("approved")
        line("OK" if appr else "WARN", "Референсы персонажа",
             f"{len(appr)} утверждено из {len(refs)}" + ("" if appr else " — выполните studio character approve <id>"))
    except CharacterError as e:
        line("FAIL", "Библиотека персонажа", str(e))

    env_file = settings.root / ".env"
    line("OK" if env_file.exists() else "WARN", ".env", "найден" if env_file.exists() else "нет — скопируйте .env.example в .env")
    for name in ("KLING_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY", "YANDEX_API_KEY", "YANDEX_FOLDER_ID",
                 "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
                 "HEDRA_API_KEY", "RUNWAYML_API_SECRET"):
        src = "" if not os.environ.get(name) or name in DOTENV_KEYS else "  ⚠ взят из переменных Windows, а не из .env"
        val = (os.environ.get(name) or "").strip()
        if name == "KLING_API_KEY" and val and not val.startswith("api-key-kling-"):
            src += "  ⚠ ключи Kling начинаются с «api-key-kling-» — похоже, вписано не то значение"
        if val and name in KNOWN_PREFIXES:
            pre = KNOWN_PREFIXES[name]
            src += f"  (начало: {val[:len(pre) + 4]}…, сверьте с кабинетом)" if val.startswith(pre) else ""
        print(f"       {name:<22} {key_shape(os.environ.get(name))}{src}")

    for name in ("kling", "hedra", "runway"):
        prov = video_provider(name, settings)
        c, why = prov.configured()
        line("OK" if c else "WARN", f"Видео: {name}", why)
        if c and check_api:
            try:
                info = prov.account_info()
                line("OK", f"  подключение {name}", redact(str(info))[:400])
            except Exception as e:  # noqa: BLE001 — диагностика должна показать любую ошибку
                line("FAIL", f"  подключение {name}", redact(str(e))[:400])
                hint = getattr(prov, "explain", lambda _e: "")(e)
                if hint:
                    print(f"       → {hint}")
    tts = tts_provider(settings.get("providers.tts", "elevenlabs"), settings)
    c, why = tts.configured()
    line("OK" if c else "WARN", f"TTS: {tts.name}", why)
    if c and check_api and tts.paid:
        try:
            line("OK", f"  подключение {tts.name}", redact(str(tts.account_info()))[:400])
        except Exception as e:  # noqa: BLE001
            line("FAIL", f"  подключение {tts.name}", redact(str(e))[:400])
    if not check_api:
        print("Проверка подключения к API (бесплатные запросы баланса): studio doctor --check-api")
    return ok
