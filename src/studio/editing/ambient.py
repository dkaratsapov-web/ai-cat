"""«Живой слой» поверх AI-сцен: движение камеры, мерцание света, огоньки-боке, пылинки, зерно.

Применяется локально при монтаже (бесплатно, без новых генераций). Кота и синхронизацию губ не меняет:
огоньки рисуются по краям кадра, центр с мордочкой остаётся чистым.
"""
from __future__ import annotations

import math
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from ..config import Settings
from .local_scenes import draw_overlays

DEFAULTS = {
    "enabled": True,
    "apply_to": ["talking", "character_motion", "ai_scene"],
    "zoom": 0.05,          # наезд камеры за сцену (5%)
    "drift": 0.012,        # лёгкий сдвиг камеры по горизонтали (доля ширины)
    "flicker": 0.035,      # амплитуда мерцания света
    "bokeh": 16,           # огоньки на фоне
    "dust": 28,            # пылинки
    "grain": 0.035,        # зерно плёнки
    "vignette": 0.28,
    "twinkle": 0.0,        # пульсация огоньков: 0 — ровный свет (без «мигающих бликов»), 1 — мерцают
}


def ambient_config(settings: Settings, scene_local: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(settings.get("video.ambient") or {})}
    local = (scene_local or {}).get("ambient")
    if local is False:
        cfg["enabled"] = False
    elif isinstance(local, dict):
        cfg.update(local)
    return cfg


def _sprite(radius: int, color: tuple[int, int, int]) -> Image.Image:
    size = radius * 4
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((radius, radius, size - radius, size - radius), fill=(*color, 255))
    return im.filter(ImageFilter.GaussianBlur(radius * 0.6))


LEVELS = 16


def _levels(spr: Image.Image) -> list[Image.Image]:
    """Заранее подготовленные варианты прозрачности — без пересчёта на каждом кадре."""
    out = []
    alpha = spr.getchannel("A")
    for k in range(LEVELS):
        im = spr.copy()
        im.putalpha(alpha.point(lambda v, f=(k + 1) / LEVELS: int(v * f)))
        out.append(im)
    return out


def _vignette(w: int, h: int, strength: float) -> Image.Image:
    small = Image.radial_gradient("L").resize((w // 4, h // 4))
    mask = small.resize((w, h), Image.BILINEAR).point(lambda v: int(min(255, max(0, (v - 90) * 1.6)) * strength))
    black = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    black.putalpha(mask)
    return black


def _edge_point(rng: random.Random, w: int, h: int) -> tuple[float, float]:
    """Точка вне центральной области (там мордочка кота)."""
    while True:
        x, y = rng.uniform(0, w), rng.uniform(0, h * 0.9)
        if not (0.2 * w < x < 0.8 * w and 0.18 * h < y < 0.62 * h):
            return x, y


def apply_ambient(src: Path, out: Path, duration: float, settings: Settings, cfg: dict, seed: str = "",
                  progress: str = "", overlays: list | None = None) -> Path:
    """Живой слой и/или надписи поверх видео. cfg["enabled"] = False — только надписи (overlays)."""
    w, h, fps = settings.get("video.width"), settings.get("video.height"), settings.get("video.fps")
    n = max(1, round(duration * fps))
    rng = random.Random(seed or src.name)
    fx = bool(cfg.get("enabled", True))
    tw = float(cfg.get("twinkle", 0.0))
    warm = [(255, 214, 140), (255, 190, 110), (255, 236, 190), (180, 210, 255)]
    bokeh = []
    for _ in range(int(cfg["bokeh"]) if fx else 0):
        x, y = _edge_point(rng, w, h)
        r = rng.randint(14, 34)
        bokeh.append({"x": x, "y": y, "spr": _levels(_sprite(r, rng.choice(warm))), "a": rng.uniform(0.3, 0.6),
                      "ph": rng.uniform(0, 6.28), "sp": rng.uniform(0.6, 1.6), "vy": rng.uniform(-14, -4)})
    dust = []
    for _ in range(int(cfg["dust"]) if fx else 0):
        x, y = _edge_point(rng, w, h)
        dust.append({"x": x, "y": y, "spr": _levels(_sprite(rng.randint(3, 5), (255, 245, 220))), "a": rng.uniform(0.6, 1.0),
                     "vx": rng.uniform(-10, 10), "vy": rng.uniform(-22, -6), "ph": rng.uniform(0, 6.28)})
    vign = _vignette(w, h, float(cfg["vignette"])) if fx and cfg.get("vignette") else None
    grain_amt = float(cfg.get("grain") or 0) if fx else 0.0
    # Заранее: зерно (несколько кадров по кругу) и слои «свет + виньетка» для разных уровней мерцания
    grains = [Image.effect_noise((w // 2, h // 2), 40).resize((w, h)).convert("RGB") for _ in range(6)] if grain_amt else []
    flick = float(cfg["flicker"]) if fx else 0.0
    light_levels = []
    for k in range(LEVELS):
        f = -flick + 2 * flick * k / (LEVELS - 1)            # от «темнее» до «светлее»
        color = (255, 228, 185, int(255 * f * 1.6)) if f > 0 else (0, 0, 0, int(255 * -f * 1.6))
        base = Image.new("RGBA", (w, h), color)
        if vign:
            base.alpha_composite(vign)
        light_levels.append(base)

    # Декодер отдаёт ровно n кадров; его служебные сообщения не выводим (иначе «Broken pipe» пугает в консоли)
    dec = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "quiet", "-i", str(src), "-frames:v", str(n),
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    enc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-frames:v", str(n),
                            "-c:v", settings.get("video.codec", "libx264"), "-preset", "veryfast",
                            "-crf", str(settings.get("video.crf", 18)), "-pix_fmt", "yuv420p", "-an", str(out)],
                           stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert dec.stdout and enc.stdin
    frame_bytes = w * h * 3
    last = None
    try:
        for i in range(n):
            raw = dec.stdout.read(frame_bytes)
            if len(raw) == frame_bytes:
                last = raw
            if last is None:
                break
            t = i / fps
            p = i / max(n - 1, 1)
            ease = p * p * (3 - 2 * p)
            img = Image.frombytes("RGB", (w, h), last)
            # камера: наезд + лёгкий дрейф (субпиксельно)
            z = 1 + (float(cfg["zoom"]) if fx else 0.0) * ease
            cw, ch = w / z, h / z
            dx = (float(cfg["drift"]) if fx else 0.0) * w * math.sin(ease * math.pi - math.pi / 2) * 0.5
            x0 = min(max((w - cw) / 2 + dx, 0), w - cw)
            y0 = (h - ch) * 0.42
            img = img.resize((w, h), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))
            # мерцание света: выбор заранее подготовленного слоя (тёплый свет / затемнение + виньетка)
            wave = 0.6 * math.sin(t * 2.1) + 0.4 * math.sin(t * 7.3 + 1.7)      # -1…1
            layer = light_levels[min(LEVELS - 1, max(0, int((wave + 1) / 2 * (LEVELS - 1) + 0.5)))].copy()
            for b in bokeh:
                a = b["a"] * (1 - 0.45 * tw + 0.45 * tw * math.sin(t * b["sp"] + b["ph"]))
                spr = b["spr"][min(LEVELS - 1, int(a * LEVELS))]
                layer.alpha_composite(spr, (int(b["x"] - spr.width / 2), int(b["y"] + b["vy"] * t - spr.height / 2)))
            for d in dust:
                a = d["a"] * (1 - 0.5 * tw + 0.5 * tw * math.sin(t * 3 + d["ph"]))
                spr = d["spr"][min(LEVELS - 1, int(a * LEVELS))]
                x = d["x"] + d["vx"] * t + 6 * math.sin(t * 1.3 + d["ph"])
                layer.alpha_composite(spr, (int(x), int(d["y"] + d["vy"] * t)))
            img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
            if grains:
                img = Image.blend(img, grains[i % len(grains)], grain_amt)
            if overlays:
                img = draw_overlays(img, settings, overlays, t)
            enc.stdin.write(img.tobytes())
            if progress and (i + 1) % max(1, n // 4) == 0:
                print(f"    живой слой {progress}: {int((i + 1) / n * 100)}%", flush=True)
        enc.stdin.close()
    except OSError:
        pass
    finally:
        dec.stdout.close()
        try:
            dec.wait(timeout=10)
        except subprocess.TimeoutExpired:
            dec.kill()
    err = enc.stderr.read().decode(errors="ignore") if enc.stderr else ""
    if enc.wait() != 0:
        raise RuntimeError(f"Живой слой: ffmpeg не смог записать {out.name}: {err[-500:]}")
    return out
