"""Генератор собственной музыки и звуковых эффектов «МяуРкетинга» (без чужих сэмплов — нет вопросов с лицензией).

Запуск (нужен numpy, только для разработчика): python tools/make_audio.py
Результат: assets/music/meow_lofi.mp3, assets/sfx/{whoosh,pop,tick}.wav; --tech → assets/music/tech_pulse.mp3
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 48000
ROOT = Path(__file__).resolve().parents[1]
rng = np.random.default_rng(7)


def midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-t / decay)


def lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    """Простой фильтр через FFT (без scipy)."""
    spec = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(x.shape[0], 1 / SR)
    gain = 1 / np.sqrt(1 + (f / cutoff) ** 4)
    return np.fft.irfft(spec * (gain[:, None] if x.ndim == 2 else gain), n=x.shape[0], axis=0)


def highpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.shape[0], 1 / SR)
    gain = 1 / np.sqrt(1 + (cutoff / np.maximum(f, 1)) ** 4)
    return np.fft.irfft(spec * gain, n=x.shape[0])


def reverb(x: np.ndarray, seconds: float = 1.6, mix: float = 0.25) -> np.ndarray:
    n = int(seconds * SR)
    ir = rng.standard_normal((n, 2)) * np.exp(-np.arange(n) / (SR * seconds / 5))[:, None]
    ir = lowpass(ir, 5000)
    ir /= np.abs(ir).sum(axis=0).max() / 6
    size = x.shape[0] + n
    wet = np.fft.irfft(np.fft.rfft(x, n=size, axis=0) * np.fft.rfft(ir, n=size, axis=0), n=size, axis=0)
    # хвост заворачиваем в начало — петля звучит бесшовно
    tail = wet[x.shape[0]:]
    wet = wet[:x.shape[0]].copy()
    wet[:tail.shape[0]] += tail
    return x * (1 - mix) + wet * mix


def epiano(freq: float, n: int) -> np.ndarray:
    t = np.arange(n) / SR
    tone = (np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * 2 * freq * t) * np.exp(-t * 3)
            + 0.12 * np.sin(2 * np.pi * 3 * freq * t) * np.exp(-t * 6))
    trem = 1 + 0.08 * np.sin(2 * np.pi * 4.5 * t)
    return tone * trem * env(n, 0.012, 1.8)


def music(out: Path) -> None:
    bpm = 84
    beat = 60 / bpm
    bar = 4 * beat
    bars = 16
    total = int(bars * bar * SR)
    mix = np.zeros((total, 2))

    def add(sig: np.ndarray, at: float, pan: float = 0.0, gain: float = 1.0) -> None:
        i = int(at * SR) % total
        sig = sig * gain
        n = min(len(sig), total - i)
        l, r = np.sqrt(0.5 * (1 - pan)), np.sqrt(0.5 * (1 + pan))
        mix[i:i + n, 0] += sig[:n] * l
        mix[i:i + n, 1] += sig[:n] * r
        if n < len(sig):  # перенос в начало — для бесшовной петли
            rest = sig[n:]
            mix[:len(rest), 0] += rest * l
            mix[:len(rest), 1] += rest * r

    # Fmaj7 — Em7 — Dm7 — Cmaj7 (тёплая «лоу-фай» прогрессия)
    chords = [[53, 57, 60, 64], [52, 55, 59, 62], [50, 53, 57, 60], [48, 52, 55, 59]]
    roots = [41, 40, 38, 36]
    melody = [72, None, 69, 67, None, 64, 67, None, 69, None, 72, 74, None, 72, 69, None]
    for b in range(bars):
        t0 = b * bar
        ch = chords[b % 4]
        for k, note in enumerate(ch):  # аккорд чуть «размазан» по времени, как живой
            add(epiano(midi(note), int(bar * SR)), t0 + 0.012 * k, pan=-0.3 + 0.2 * k, gain=0.16)
        add(epiano(midi(ch[1] + 12), int(beat * 2 * SR)), t0 + 2.5 * beat, pan=0.2, gain=0.06)
        # бас
        for bt, g in ((0, 0.5), (2.5, 0.3)):
            n = int(beat * 1.4 * SR)
            tb = np.arange(n) / SR
            add(np.sin(2 * np.pi * midi(roots[b % 4]) * tb) * env(n, 0.01, 0.6), t0 + bt * beat, gain=g)
        # барабаны (со 2-го такта)
        if b >= 1:
            for bt in (0, 2.5):
                n = int(0.35 * SR)
                tk = np.arange(n) / SR
                f = 45 + 75 * np.exp(-tk * 30)
                add(np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.002, 0.12), t0 + bt * beat, gain=0.5)
            for bt in (1, 3):
                n = int(0.25 * SR)
                sn = lowpass(rng.standard_normal(n), 2500) * env(n, 0.002, 0.06)
                add(sn, t0 + bt * beat, gain=0.18)
            for e in range(8):
                swing = 0.06 * beat if e % 2 else 0.0
                n = int(0.05 * SR)
                hh = highpass(rng.standard_normal(n), 7000) * env(n, 0.001, 0.015)
                add(hh, t0 + e * beat / 2 + swing, pan=0.35, gain=0.10 if e % 2 else 0.14)
        # редкая мелодия-колокольчик
        if b % 2 == 1:
            for s, note in enumerate(melody[(b // 2 % 2) * 8:(b // 2 % 2) * 8 + 8]):
                if note:
                    n = int(beat * 1.2 * SR)
                    tm = np.arange(n) / SR
                    bell = (np.sin(2 * np.pi * midi(note) * tm) + 0.2 * np.sin(2 * np.pi * midi(note) * 4 * tm)) * env(n, 0.005, 0.5)
                    add(bell, t0 + s * beat / 2, pan=0.25, gain=0.07)
    mix = reverb(mix, 1.8, 0.22)
    mix = lowpass(mix, 6500)  # «тёплый» лоу-фай
    crackle = np.zeros((total, 2))
    pops = rng.integers(0, total, size=int(bars * bar * 6))
    crackle[pops] = rng.standard_normal((len(pops), 2)) * 0.15
    mix += lowpass(crackle, 3000) + rng.standard_normal((total, 2)) * 0.002
    mix /= np.abs(mix).max() / 0.6
    write_wav(out.with_suffix(".wav"), mix)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(out.with_suffix(".wav")), "-af", "loudnorm=I=-18:TP=-2",
                    "-ar", str(SR), "-c:a", "libmp3lame", "-b:a", "160k", str(out)], check=True)
    out.with_suffix(".wav").unlink()


def whoosh() -> np.ndarray:
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    out = np.zeros(n)
    y = 0.0
    for i in range(n):  # фильтр с плавающей частотой среза: 300 → 4000 → 600 Гц
        p = i / n
        fc = 300 + 3700 * np.sin(np.pi * p) ** 2
        a = 1 - np.exp(-2 * np.pi * fc / SR)
        y += a * (noise[i] - y)
        out[i] = y
    out *= np.sin(np.pi * t / t[-1]) ** 2
    pan = np.linspace(-0.6, 0.6, n)
    st = np.stack([out * np.sqrt(0.5 * (1 - pan)), out * np.sqrt(0.5 * (1 + pan))], axis=1)
    return st / np.abs(st).max() * 0.8


def pop() -> np.ndarray:
    n = int(0.14 * SR)
    t = np.arange(n) / SR
    f = 300 + 700 * np.exp(-t * 40)
    s = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 0.001, 0.035)
    return np.stack([s, s], axis=1) / np.abs(s).max() * 0.8


def tick() -> np.ndarray:
    n = int(0.07 * SR)
    t = np.arange(n) / SR
    s = (np.sin(2 * np.pi * 1760 * t) + 0.4 * np.sin(2 * np.pi * 2640 * t)) * env(n, 0.001, 0.012)
    return np.stack([s, s], axis=1) / np.abs(s).max() * 0.6


def write_wav(path: Path, data: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(pcm.shape[1] if pcm.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())



def saw(freq: float, n: int, harmonics: int = 12) -> np.ndarray:
    t = np.arange(n) / SR
    return sum(((-1) ** (k + 1)) * np.sin(2 * np.pi * freq * k * t) / k for k in range(1, harmonics + 1)) * 0.6


def tech(out: Path, bpm: int = 118, bars: int = 32) -> None:
    """Лёгкий технологичный трек: ровный бочка-бит, офбит-хэты, плак-арпеджио, мягкий пэд, саб-бас. Без вокала."""
    beat = 60 / bpm
    bar = 4 * beat
    total = int(bars * bar * SR)
    mix = np.zeros((total, 2))

    def add(sig, at, pan=0.0, gain=1.0):
        i = int(at * SR) % total
        sig = sig * gain
        n = min(len(sig), total - i)
        l, r = np.sqrt(0.5 * (1 - pan)), np.sqrt(0.5 * (1 + pan))
        mix[i:i + n, 0] += sig[:n] * l
        mix[i:i + n, 1] += sig[:n] * r
        if n < len(sig):
            mix[:len(sig) - n, 0] += sig[n:] * l
            mix[:len(sig) - n, 1] += sig[n:] * r

    chords = [[57, 60, 64], [53, 57, 60], [48, 52, 55], [55, 59, 62]]   # Am F C G
    roots = [45, 41, 48, 43]
    duck = np.ones(total)   # «пампинг» пэда от бочки
    for b in range(bars):
        t0 = b * bar
        ch = chords[(b // 2) % 4]
        section = b // 8   # 0 интро, 1-3 — полный
        # пэд
        n = int(bar * SR)
        pad = sum(lowpass(saw(midi(nn), n, 8), 1800) for nn in ch) * env(n, 0.25, 6.0)
        add(pad, t0, pan=-0.2, gain=0.05)
        add(pad, t0 + 0.012, pan=0.2, gain=0.05)
        for k in range(4):
            if section >= 0:
                nk = int(0.4 * SR)
                tk = np.arange(nk) / SR
                f = 48 + 90 * np.exp(-tk * 35)
                add(np.sin(2 * np.pi * np.cumsum(f) / SR) * env(nk, 0.002, 0.14), t0 + k * beat, gain=0.55 if section else 0.35)
                i0 = int((t0 + k * beat) * SR) % total
                m = min(int(0.25 * SR), total - i0)
                duck[i0:i0 + m] = np.minimum(duck[i0:i0 + m], 0.45 + 0.55 * np.linspace(0, 1, m) ** 0.6)
            if section >= 1:
                nh = int(0.06 * SR)
                add(highpass(rng.standard_normal(nh), 8000) * env(nh, 0.001, 0.02), t0 + k * beat + beat / 2, pan=0.3, gain=0.16)
            if section >= 1 and k in (1, 3):
                nc = int(0.2 * SR)
                add(lowpass(highpass(rng.standard_normal(nc), 900), 5000) * env(nc, 0.002, 0.05), t0 + k * beat, gain=0.14)
        # саб-бас на восьмых
        for e in range(8):
            if section >= 1 or e % 2 == 0:
                nb = int(beat / 2 * SR * 0.9)
                tb = np.arange(nb) / SR
                add(np.sin(2 * np.pi * midi(roots[(b // 2) % 4]) * tb) * env(nb, 0.005, 0.18), t0 + e * beat / 2, gain=0.32)
        # плак-арпеджио шестнадцатыми
        if section >= 1:
            arp = [ch[0] + 12, ch[1] + 12, ch[2] + 12, ch[1] + 12]
            for sx in range(16):
                npk = int(beat / 4 * SR * 1.6)
                tone = lowpass(saw(midi(arp[sx % 4]), npk, 6), 3200) * env(npk, 0.002, 0.09)
                add(tone, t0 + sx * beat / 4, pan=0.35 if sx % 2 else -0.35, gain=0.06)
    mix[:, 0] *= duck
    mix[:, 1] *= duck
    mix = reverb(mix, 1.2, 0.15)
    mix /= np.abs(mix).max() / 0.7
    write_wav(out.with_suffix(".wav"), mix)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(out.with_suffix(".wav")), "-af", "loudnorm=I=-16:TP=-1.5",
                    "-ar", str(SR), "-c:a", "libmp3lame", "-b:a", "192k", str(out)], check=True)
    out.with_suffix(".wav").unlink()


if __name__ == "__main__":
    import sys
    if "--tech" in sys.argv:
        tech(ROOT / "assets/music/tech_pulse.mp3")
        raise SystemExit
    music(ROOT / "assets/music/meow_lofi.mp3")
    for name, fn in (("whoosh", whoosh), ("pop", pop), ("tick", tick)):
        write_wav(ROOT / f"assets/sfx/{name}.wav", fn())
    print("Готово: assets/music/meow_lofi.mp3, assets/sfx/whoosh.wav, pop.wav, tick.wav")
