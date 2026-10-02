"""Озвучка: ElevenLabs (основной), ручная дорожка, тестовая «тишина».

ElevenLabs (официальный SDK github.com/elevenlabs/elevenlabs-python):
  POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?output_format=...
  заголовок xi-api-key; тело: text, model_id, language_code, voice_settings{stability, similarity_boost,
  style, use_speaker_boost, speed}, previous_text/next_text (для связной интонации между фрагментами)
  ответ: audio_base64 + alignment{characters, character_start_times_seconds, character_end_times_seconds}
  GET /v1/user/subscription -> character_count / character_limit (бесплатно)
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from ..config import secret
from ..editing import ffmpeg
from .base import NotConfiguredError, ProviderError, TTSProvider, TTSResult, http_json

EL_BASE = "https://api.elevenlabs.io"


def to_wav(src: Path, dest: Path, rate: int = 48000) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                    "-ac", "1", "-ar", str(rate), str(dest)], check=True)
    return dest


def words_from_alignment(alignment: dict) -> list[tuple[str, float, float]]:
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[tuple[str, float, float]] = []
    cur, ws, we = "", None, None
    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append((cur, ws, we))  # type: ignore[arg-type]
            cur, ws, we = "", None, None
            continue
        if not cur:
            ws = s
        cur += ch
        we = e
    if cur:
        words.append((cur, ws, we))  # type: ignore[arg-type]
    return words


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"

    def __init__(self, settings=None):
        self.settings = settings

    def configured(self) -> tuple[bool, str]:
        if not secret("ELEVENLABS_API_KEY"):
            return False, "Нет ELEVENLABS_API_KEY в .env"
        return True, "API Key"

    def _headers(self) -> dict[str, str]:
        key = secret("ELEVENLABS_API_KEY")
        if not key:
            raise NotConfiguredError("Нет ELEVENLABS_API_KEY")
        return {"xi-api-key": key, "Content-Type": "application/json"}

    @staticmethod
    def voice_id(preset: dict) -> str:
        vid = preset.get("voice_id") or os.environ.get("ELEVENLABS_VOICE_ID")
        if not vid:
            raise NotConfiguredError("Не задан voice_id: config/voices.yaml или ELEVENLABS_VOICE_ID в .env")
        return vid

    def estimate_usd(self, text: str, preset: dict, pricing: dict) -> float:
        p = pricing.get("elevenlabs", {})
        mult = p.get("credits_per_char", {}).get(preset.get("model_id"), 1.0)
        return round(len(text) * mult / 1000 * float(p.get("usd_per_1k_chars", 0.18)), 4)

    def synthesize(self, text: str, preset: dict, dest: Path, previous_text: str = "", next_text: str = "") -> TTSResult:
        body: dict[str, Any] = {
            "text": text,
            "model_id": preset.get("model_id", "eleven_multilingual_v2"),
            "voice_settings": preset.get("voice_settings") or {},
        }
        if preset.get("language_code"):
            body["language_code"] = preset["language_code"]
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text
        data = http_json(
            "POST", f"{EL_BASE}/v1/text-to-speech/{self.voice_id(preset)}/with-timestamps",
            headers=self._headers(), json_body=body,
            params={"output_format": preset.get("output_format", "mp3_44100_128")},
            timeout=120, retries=2,
        )
        audio_b64 = data.get("audio_base64")
        if not audio_b64:
            raise ProviderError(f"ElevenLabs не вернул аудио: {str(data)[:200]}")
        raw = dest.with_suffix(".src.mp3")
        raw.write_bytes(base64.b64decode(audio_b64))
        to_wav(raw, dest)
        words = words_from_alignment(data.get("alignment") or {})
        return TTSResult(audio_path=dest, duration=ffmpeg.duration(dest), words=words, characters=len(text))

    def account_info(self) -> dict[str, Any]:
        d = http_json("GET", f"{EL_BASE}/v1/user/subscription", headers=self._headers(), retries=1)
        used, limit = d.get("character_count"), d.get("character_limit")
        return {"tier": d.get("tier"), "character_count": used, "character_limit": limit,
                "remaining": (limit - used) if isinstance(limit, int) and isinstance(used, int) else None}


class ManualTTS(TTSProvider):
    """Собственная дорожка: файлы projects/<ep>/imports/audio/<scene>.(wav|mp3|m4a)."""
    name = "manual"
    paid = False

    def __init__(self, settings=None, import_dir: Path | None = None):
        self.import_dir = import_dir

    def configured(self) -> tuple[bool, str]:
        return True, "ручной импорт аудио"

    def synthesize(self, text: str, preset: dict, dest: Path, **_: Any) -> TTSResult:
        scene_id = dest.stem
        base = self.import_dir or dest.parent.parent / "imports" / "audio"
        for ext in ("wav", "mp3", "m4a", "aac", "ogg"):
            src = base / f"{scene_id}.{ext}"
            if src.exists():
                to_wav(src, dest)
                return TTSResult(audio_path=dest, duration=ffmpeg.duration(dest), characters=len(text))
        raise ProviderError(f"Нет файла озвучки для {scene_id}: положите {base}/{scene_id}.wav (или .mp3)")


class MockTTS(TTSProvider):
    """Бесплатная заглушка для тестового прогона: тихий тон нужной длительности по оценке темпа речи."""
    name = "mock"
    paid = False

    def __init__(self, settings=None, words_per_sec: float = 2.6):
        self.wps = words_per_sec

    def configured(self) -> tuple[bool, str]:
        return True, "тестовая заглушка (без реального голоса)"

    def synthesize(self, text: str, preset: dict, dest: Path, **_: Any) -> TTSResult:
        tokens = re.findall(r"\S+", text)
        dur = max(1.0, len(tokens) / self.wps + 0.3)
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", f"sine=frequency=220:sample_rate=48000:duration={dur:.3f}",
                        "-af", "volume=0.05", "-ac", "1", str(dest)], check=True)
        # Равномерные тайминги слов
        words, t = [], 0.15
        step = (dur - 0.3) / max(len(tokens), 1)
        for w in tokens:
            words.append((w, t, t + step))
            t += step
        return TTSResult(audio_path=dest, duration=dur, words=words, characters=len(text))
