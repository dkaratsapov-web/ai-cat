"""Реестр адаптеров. Новый сервис = новый класс + строка в реестре."""
from __future__ import annotations

from .base import TTSProvider, VideoProvider


def video_provider(name: str, settings) -> VideoProvider:
    if name == "kling":
        from .kling import KlingProvider
        return KlingProvider(settings)
    if name == "hedra":
        from .hedra import HedraProvider
        return HedraProvider(settings)
    if name == "higgsfield":
        from .higgsfield import HiggsfieldProvider
        return HiggsfieldProvider(settings)
    if name == "runway":
        from .runway import RunwayProvider
        return RunwayProvider(settings)
    if name == "mock":
        from .mock import MockProvider
        return MockProvider(settings)
    raise ValueError(f"Неизвестный видеопровайдер: {name}")


def tts_provider(name: str, settings) -> TTSProvider:
    from .tts import ElevenLabsTTS, ManualTTS, MockTTS, OpenAITTS, SaluteTTS, YandexTTS
    if name == "yandex":
        return YandexTTS(settings)
    if name == "elevenlabs":
        return ElevenLabsTTS(settings)
    if name == "salute":
        return SaluteTTS(settings)
    if name == "openai":
        return OpenAITTS(settings)
    if name == "manual":
        return ManualTTS(settings)
    if name == "mock":
        return MockTTS(settings)
    raise ValueError(f"Неизвестный TTS-провайдер: {name}")


VIDEO_PROVIDERS = ("kling", "higgsfield", "hedra", "runway", "mock")
TTS_PROVIDERS = ("yandex", "openai", "salute", "elevenlabs", "manual", "mock")
