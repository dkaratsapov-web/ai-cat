"""Озвучка: Yandex SpeechKit (основной), ElevenLabs, ручная дорожка, тестовая «тишина».

ElevenLabs (официальный SDK github.com/elevenlabs/elevenlabs-python):
  POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?output_format=...
  заголовок xi-api-key; тело: text, model_id, language_code, voice_settings{stability, similarity_boost,
  style, use_speaker_boost, speed}, previous_text/next_text (для связной интонации между фрагментами)
  ответ: audio_base64 + alignment{characters, character_start_times_seconds, character_end_times_seconds}
  GET /v1/user/subscription -> character_count / character_limit (бесплатно)
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import requests

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


YC_TTS_URL = "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"
YC_UNIT_CHARS = 250  # API v3 тарифицирует запросами по 250 символов с округлением вверх


def parse_yandex_stream(raw: str) -> list[dict]:
    """Ответ REST-шлюза — поток JSON-объектов (по одному на строку), каждый обычно обёрнут в {"result": ...}."""
    raw = raw.strip()
    if not raw:
        return []
    chunks: list[dict] = []
    try:
        whole = json.loads(raw)
        items = whole if isinstance(whole, list) else [whole]
    except ValueError:
        items = [json.loads(line) for line in raw.splitlines() if line.strip()]
    for it in items:
        if "error" in it:
            raise ProviderError(f"Yandex SpeechKit: {str(it['error'])[:300]}")
        chunks.append(it.get("result", it))
    return chunks


class YandexTTS(TTSProvider):
    """Yandex SpeechKit API v3 (REST): POST tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis.

    Источник: официальные спецификации github.com/yandex-cloud/cloudapi (yandex/cloud/ai/tts/v3) и SDK
    yandex-speechkit. Авторизация: `Authorization: Api-Key <ключ сервисного аккаунта>`
    (или IAM-токен + x-folder-id). Подсказки: voice, role, speed; в ответе — аудио и пословные тайминги.
    """
    name = "yandex"

    def __init__(self, settings=None):
        self.settings = settings

    def configured(self) -> tuple[bool, str]:
        if secret("YANDEX_API_KEY"):
            return True, "API-ключ сервисного аккаунта"
        if secret("YANDEX_IAM_TOKEN") and secret("YANDEX_FOLDER_ID"):
            return True, "IAM-токен + folder id"
        return False, "Нет YANDEX_API_KEY в .env"

    def _headers(self) -> dict[str, str]:
        key = secret("YANDEX_API_KEY")
        if key:
            h = {"Authorization": f"Api-Key {key}"}
        elif secret("YANDEX_IAM_TOKEN"):
            h = {"Authorization": f"Bearer {secret('YANDEX_IAM_TOKEN')}"}
        else:
            raise NotConfiguredError("Нет YANDEX_API_KEY")
        if secret("YANDEX_FOLDER_ID"):
            h["x-folder-id"] = secret("YANDEX_FOLDER_ID")  # type: ignore[assignment]
        h["Content-Type"] = "application/json"
        return h

    @staticmethod
    def billing_units(text: str) -> int:
        return max(1, -(-len(text) // YC_UNIT_CHARS))

    def estimate_usd(self, text: str, preset: dict, pricing: dict) -> float:
        p = pricing.get("yandex", {})
        rub = self.billing_units(text) * float(p.get("rub_per_unit", 0.1626))
        return round(rub / float(p.get("rub_per_usd", 90)), 5)

    def synthesize(self, text: str, preset: dict, dest: Path, **_: Any) -> TTSResult:
        voice = preset.get("voice") or os.environ.get("YANDEX_VOICE")
        if not voice:
            raise NotConfiguredError("Не задан голос: voice в config/voices.yaml или YANDEX_VOICE в .env")
        hints: list[dict[str, Any]] = [{"voice": voice}]
        if preset.get("role"):
            hints.append({"role": preset["role"]})
        if preset.get("speed"):
            hints.append({"speed": float(preset["speed"])})
        if preset.get("pitch_shift"):
            hints.append({"pitchShift": float(preset["pitch_shift"])})
        body = {
            "text": text,
            "hints": hints,
            "outputAudioSpec": {"containerAudio": {"containerAudioType": "WAV"}},
            "loudnessNormalizationType": "LUFS",
            "unsafeMode": True,  # тексты > 250 символов / 24 с не падают, а тарифицируются по 250 символов
        }
        try:
            r = requests.post(YC_TTS_URL, headers=self._headers(), json=body, timeout=120)
        except requests.RequestException as e:
            raise ProviderError(f"Yandex SpeechKit недоступен: {e}", retryable=True) from e
        if r.status_code >= 400:
            raise ProviderError(f"Yandex SpeechKit HTTP {r.status_code}: {r.text[:300]}", status=r.status_code)
        chunks = parse_yandex_stream(r.text)
        # Каждый чанк — отдельный WAV; несколько склеиваем через ffmpeg concat, чтобы не испортить заголовки
        parts = [base64.b64decode(c["audioChunk"]["data"]) for c in chunks if c.get("audioChunk")]
        if not parts:
            raise ProviderError("Yandex SpeechKit не вернул аудио")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if len(parts) == 1:
            src = dest.with_suffix(".src.wav")
            src.write_bytes(parts[0])
        else:
            files = []
            for i, data in enumerate(parts):
                pth = dest.with_suffix(f".part{i}.wav")
                pth.write_bytes(data)
                files.append(pth)
            lst = dest.with_suffix(".parts.txt")
            lst.write_text("".join(f"file '{p.resolve()}'\n" for p in files), encoding="utf-8")
            src = dest.with_suffix(".src.wav")
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(lst), str(src)], check=True)
        to_wav(src, dest)
        words = []
        for c in chunks:
            for w in c.get("wordTimings") or []:
                start = int(w.get("startMs", 0)) / 1000
                words.append((w.get("word", ""), start, start + int(w.get("lengthMs", 0)) / 1000))
        return TTSResult(audio_path=dest, duration=ffmpeg.duration(dest), words=words, characters=len(text))

    def account_info(self) -> dict[str, Any]:
        # Бесплатного метода проверки баланса у SpeechKit нет; ключ проверяется первой (копеечной) озвучкой.
        return {"note": "баланс смотрите в консоли Yandex Cloud → Биллинг"}


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
