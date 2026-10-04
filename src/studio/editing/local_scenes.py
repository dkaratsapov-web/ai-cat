"""Бесплатные локальные сцены: карточки, анимированные графики, скриншоты, «Кен Бёрнс» по фото.

Кадры рисуются Pillow и передаются в FFmpeg через pipe (rawvideo). Так получается плавная
субпиксельная анимация без платных генераций.
"""
from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter

from ..config import Settings
from ..models import Scene
from . import fonts

Frame = Callable[[float], Image.Image]


class LocalRenderError(RuntimeError):
    pass


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def ease_out(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def write_frames(out: Path, frame_fn: Frame, duration: float, settings: Settings) -> Path:
    w, h, fps = settings.get("video.width"), settings.get("video.height"), settings.get("video.fps")
    n = max(1, round(duration * fps))
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
        "-frames:v", str(n), "-c:v", settings.get("video.codec", "libx264"), "-preset", "veryfast",
        "-crf", str(settings.get("video.crf", 18)), "-pix_fmt", "yuv420p", "-an", str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin
    try:
        for i in range(n):
            img = frame_fn(i / fps)
            if img.size != (w, h):
                img = img.resize((w, h))
            proc.stdin.write(img.convert("RGB").tobytes())
        proc.stdin.close()
    except OSError:  # BrokenPipeError на Linux, OSError(EINVAL) на Windows — ffmpeg завершился раньше
        pass
    err = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
    if proc.wait() != 0:
        raise LocalRenderError(f"FFmpeg не смог записать {out.name}: {err[-800:]}")
    return out


# ---------------------------------------------------------------- helpers

def cover(img: Image.Image, w: int, h: int) -> Image.Image:
    scale = max(w / img.width, h / img.height)
    r = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    x, y = (r.width - w) // 2, (r.height - h) // 2
    return r.crop((x, y, x + w, y + h))


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines: list[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split():
            test = f"{cur} {word}".strip()
            if draw.textlength(test, font=font) <= max_w or not cur:
                cur = test
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def background(settings: Settings, w: int, h: int) -> Image.Image:
    base = hex_rgb(settings.get("branding.background_color", "#14161C"))
    img = Image.new("RGB", (w, h), base)
    # мягкий вертикальный градиент
    top = tuple(min(255, c + 18) for c in base)
    grad = Image.linear_gradient("L").resize((w, h))
    return Image.composite(img, Image.new("RGB", (w, h), top), grad)


def rubric_badge(draw: ImageDraw.ImageDraw, settings: Settings, x: int, y: int) -> None:
    f = fonts.font(settings.fonts_dir, 40)
    text = settings.get("branding.rubric", "")
    if not text:
        return
    accent = hex_rgb(settings.get("branding.accent_color", "#FFD43B"))
    tw = draw.textlength(text, font=f)
    draw.rounded_rectangle((x, y, x + tw + 48, y + 72), radius=36, fill=accent)
    draw.text((x + 24, y + 12), text, font=f, fill=(20, 20, 20))


def resolve_image(project_path: Path, settings: Settings, rel: str) -> Path:
    """Картинка сцены: только внутри imports/ и images/ проекта или assets/ — и только настоящее изображение.

    Защита от отправки в платный API произвольного файла (например, .env) под видом кадра."""
    bases = [project_path / "imports", project_path / "images", settings.assets_dir]
    for base in bases:
        base_r = base.resolve()
        p = (base / rel).resolve()
        if not p.is_relative_to(base_r) or not p.is_file():
            continue
        try:
            with Image.open(p) as im:
                im.verify()
        except Exception as e:  # noqa: BLE001
            raise LocalRenderError(f"{rel}: это не изображение ({e})") from e
        return p
    raise LocalRenderError(f"Изображение не найдено: {rel}. Положите его в imports/ проекта "
                           "(разрешены только imports/, images/ проекта и assets/)")


# ---------------------------------------------------------------- kinds

def card_frames(scene: Scene, settings: Settings) -> Frame:
    """local: {kind: card, title: str, bullets: [..], footnote: str}"""
    w, h = settings.get("video.width"), settings.get("video.height")
    sa = settings.get("safe_area")
    cfg = scene.local
    bg = background(settings, w, h)
    d = ImageDraw.Draw(bg)
    rubric_badge(d, settings, sa["left"], sa["top"])
    title_f = fonts.font(settings.fonts_dir, int(cfg.get("title_size", 92)))
    bullet_f = fonts.font(settings.fonts_dir, int(cfg.get("bullet_size", 62)))
    note_f = fonts.font(settings.fonts_dir, 38, bold=False)
    text_c = hex_rgb(settings.get("branding.text_color"))
    accent = hex_rgb(settings.get("branding.accent_color"))
    muted = hex_rgb(settings.get("branding.muted_color"))
    max_w = w - sa["left"] - sa["right"]
    y = sa["top"] + 140
    for line in wrap(d, cfg.get("title", ""), title_f, max_w):
        d.text((sa["left"], y), line, font=title_f, fill=text_c)
        y += int(title_f.size * 1.15)
    y += 30
    d.rectangle((sa["left"], y, sa["left"] + 160, y + 10), fill=accent)
    y += 70
    base = bg.copy()
    bullets = cfg.get("bullets", []) or []
    # Позиции пунктов заранее
    layout = []
    for b in bullets:
        lines = wrap(d, b, bullet_f, max_w - 90)
        layout.append((y, lines))
        y += int(bullet_f.size * 1.2) * len(lines) + 40
    if cfg.get("footnote"):
        d2 = ImageDraw.Draw(base)
        fy = h - sa["bottom"] - 60
        for line in wrap(d2, cfg["footnote"], note_f, max_w):
            d2.text((sa["left"], fy), line, font=note_f, fill=muted)
            fy += 46
    reveal = float(cfg.get("reveal_every", max(0.4, (scene.duration - 0.6) / max(len(bullets), 1))))

    def frame(t: float) -> Image.Image:
        img = base.copy()
        dd = ImageDraw.Draw(img, "RGBA")
        for i, (by, lines) in enumerate(layout):
            p = ease_out((t - 0.3 - i * reveal) / 0.35)
            if p <= 0:
                continue
            a = int(255 * p)
            dx = int((1 - p) * 60)
            dd.ellipse((sa["left"] + dx, by + 14, sa["left"] + 44 + dx, by + 58), fill=(*accent, a))
            ly = by
            for line in lines:
                dd.text((sa["left"] + 80 + dx, ly), line, font=bullet_f, fill=(*text_c, a))
                ly += int(bullet_f.size * 1.2)
        return img

    return frame


def chart_frames(scene: Scene, settings: Settings) -> Frame:
    """local: {kind: chart, title, labels: [...], values: [...], unit: '₽', demo: true}"""
    w, h = settings.get("video.width"), settings.get("video.height")
    sa = settings.get("safe_area")
    cfg = scene.local
    labels = [str(x) for x in cfg.get("labels", [])]
    values = [float(v) for v in cfg.get("values", [])]
    if not labels or len(labels) != len(values):
        raise LocalRenderError(f"{scene.id}: для графика нужны labels и values одинаковой длины")
    unit = cfg.get("unit", "")
    bg = background(settings, w, h)
    d = ImageDraw.Draw(bg)
    rubric_badge(d, settings, sa["left"], sa["top"])
    title_f = fonts.font(settings.fonts_dir, 80)
    label_f = fonts.font(settings.fonts_dir, 44)
    value_f = fonts.font(settings.fonts_dir, 54)
    note_f = fonts.font(settings.fonts_dir, 36, bold=False)
    text_c = hex_rgb(settings.get("branding.text_color"))
    accent = hex_rgb(settings.get("branding.accent_color"))
    muted = hex_rgb(settings.get("branding.muted_color"))
    max_w = w - sa["left"] - sa["right"]
    y = sa["top"] + 140
    for line in wrap(d, cfg.get("title", ""), title_f, max_w):
        d.text((sa["left"], y), line, font=title_f, fill=text_c)
        y += 94
    if cfg.get("demo", True):
        d.text((sa["left"], y + 10), "* значения условные, для примера", font=note_f, fill=muted)
    top = y + 120
    bottom = h - sa["bottom"] - 140
    n = len(values)
    gap = 40
    bw = (max_w - gap * (n - 1)) / n
    vmax = max(values) or 1
    highlight = cfg.get("highlight")  # индекс выделяемого столбца
    base = bg.copy()
    bd = ImageDraw.Draw(base)
    bd.line((sa["left"], bottom, w - sa["right"], bottom), fill=muted, width=3)
    for i, lab in enumerate(labels):
        x0 = sa["left"] + i * (bw + gap)
        for j, line in enumerate(wrap(bd, lab, label_f, int(bw + gap - 10))[:2]):
            tw = bd.textlength(line, font=label_f)
            bd.text((x0 + (bw - tw) / 2, bottom + 20 + j * 50), line, font=label_f, fill=text_c)

    def frame(t: float) -> Image.Image:
        img = base.copy()
        dd = ImageDraw.Draw(img)
        for i, v in enumerate(values):
            p = ease_out((t - 0.2 - i * 0.12) / 0.8)
            bh = (bottom - top - 80) * (v / vmax) * p
            x0 = sa["left"] + i * (bw + gap)
            color = accent if (highlight is None or highlight == i) else muted
            if bh > 1:
                dd.rounded_rectangle((x0, bottom - bh, x0 + bw, bottom), radius=14, fill=color)
            if p > 0.05:
                val = v * p
                txt = f"{val:,.0f}".replace(",", " ") + (f" {unit}" if unit else "")
                tw = dd.textlength(txt, font=value_f)
                dd.text((x0 + (bw - tw) / 2, bottom - bh - 70), txt, font=value_f, fill=text_c)
        return img

    return frame


def kenburns_frames(img: Image.Image, scene: Scene, settings: Settings, *, watermark: str | None = None) -> Frame:
    """Плавный зум/панорама по изображению (cover-кадрирование в 9:16)."""
    w, h = settings.get("video.width"), settings.get("video.height")
    cfg = scene.local
    z0, z1 = float(cfg.get("zoom_from", 1.0)), float(cfg.get("zoom_to", 1.08))
    fx, fy = cfg.get("focus", [0.5, 0.45])  # точка фокуса в долях кадра
    src = cover(img.convert("RGB"), w, h)
    # Запас разрешения для субпиксельного зума
    big = src.resize((w * 2, h * 2), Image.LANCZOS)
    dur = max(scene.duration, 0.1)
    wm_f = fonts.font(settings.fonts_dir, 48) if watermark else None

    def frame(t: float) -> Image.Image:
        p = t / dur
        p = p * p * (3 - 2 * p)  # smoothstep
        z = z0 + (z1 - z0) * p
        cw, ch = big.width / z, big.height / z
        cx = big.width * fx
        cy = big.height * fy
        x0 = min(max(cx - cw / 2, 0), big.width - cw)
        y0 = min(max(cy - ch / 2, 0), big.height - ch)
        out = big.resize((w, h), Image.BICUBIC, box=(x0, y0, x0 + cw, y0 + ch))
        if watermark:
            dd = ImageDraw.Draw(out, "RGBA")
            dd.rectangle((0, h // 2 - 50, w, h // 2 + 50), fill=(200, 0, 0, 150))
            tw = dd.textlength(watermark, font=wm_f)
            dd.text(((w - tw) / 2, h // 2 - 30), watermark, font=wm_f, fill=(255, 255, 255, 255))
        return out

    return frame


def redact_boxes(img: Image.Image, boxes: list) -> Image.Image:
    """Обезличивание: сильное размытие прямоугольников [x, y, w, h] (в пикселях исходного скриншота)."""
    if not boxes:
        return img
    out = img.copy()
    for b in boxes:
        x, y, bw, bh = [int(v) for v in b]
        region = out.crop((x, y, x + bw, y + bh))
        # пикселизация + размытие — текст не восстанавливается
        small = region.resize((max(1, bw // 16), max(1, bh // 16)), Image.BILINEAR)
        region = small.resize((bw, bh), Image.NEAREST).filter(ImageFilter.GaussianBlur(6))
        out.paste(region, (x, y))
    return out


def screenshot_frames(img: Image.Image, scene: Scene, settings: Settings) -> Frame:
    """local: {kind: screenshot, image: path, highlights: [{box: [x,y,w,h], at: 1.0, label: '...'}], caption,
               redact: [[x,y,w,h], ...]}  — redact размывает конфиденциальные области

    Скриншот вписывается по ширине на размытом фоне, медленно приближается,
    поверх появляются рамки-акценты (координаты — в пикселях исходного скриншота).
    """
    w, h = settings.get("video.width"), settings.get("video.height")
    sa = settings.get("safe_area")
    cfg = scene.local
    accent = hex_rgb(settings.get("branding.accent_color"))
    img = redact_boxes(img.convert("RGB"), cfg.get("redact", []))
    blur = cover(img.convert("RGB"), w, h).filter(ImageFilter.GaussianBlur(40))
    blur = Image.blend(blur, Image.new("RGB", (w, h), (0, 0, 0)), 0.45)
    inner_w = w - 2 * 50
    scale = inner_w / img.width
    max_h = h - sa["top"] - sa["bottom"] + 120
    if img.height * scale > max_h:
        scale = max_h / img.height
    shot = img.convert("RGB").resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    sx, sy = (w - shot.width) // 2, sa["top"] + (max_h - 120 - shot.height) // 2 + 60
    base = blur.copy()
    shadow = Image.new("RGBA", (shot.width + 40, shot.height + 40), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((20, 20, shot.width + 20, shot.height + 20), radius=24, fill=(0, 0, 0, 160))
    base.paste(shadow.filter(ImageFilter.GaussianBlur(16)), (sx - 20, sy - 4), shadow.filter(ImageFilter.GaussianBlur(16)))
    base.paste(shot, (sx, sy))
    cap_f = fonts.font(settings.fonts_dir, 60)
    lab_f = fonts.font(settings.fonts_dir, 44)
    if cfg.get("caption"):
        d = ImageDraw.Draw(base)
        yy = sa["top"] - 10
        for line in wrap(d, cfg["caption"], cap_f, w - sa["left"] - sa["right"]):
            d.text((sa["left"], yy), line, font=cap_f, fill=(255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0))
            yy += 70
    highlights = cfg.get("highlights", []) or []
    z1 = float(cfg.get("zoom_to", 1.05))
    dur = max(scene.duration, 0.1)

    def frame(t: float) -> Image.Image:
        img2 = base.copy()
        dd = ImageDraw.Draw(img2, "RGBA")
        for hl in highlights:
            at = float(hl.get("at", 0.5))
            if t < at:
                continue
            x, y, bw, bh = [v * scale for v in hl["box"]]
            pulse = 0.5 + 0.5 * math.sin((t - at) * 6)
            width = int(6 + 4 * pulse)
            dd.rounded_rectangle((sx + x - 8, sy + y - 8, sx + x + bw + 8, sy + y + bh + 8), radius=16,
                                 outline=(*accent, 255), width=width, fill=(*accent, 40))
            if hl.get("label"):
                lx, ly = sx + x, sy + y + bh + 20
                tw = dd.textlength(hl["label"], font=lab_f)
                lx = min(lx, w - tw - 60)
                dd.rounded_rectangle((lx - 16, ly, lx + tw + 16, ly + 64), radius=14, fill=(*accent, 240))
                dd.text((lx, ly + 8), hl["label"], font=lab_f, fill=(20, 20, 20))
        p = t / dur
        z = 1 + (z1 - 1) * (p * p * (3 - 2 * p))
        if z > 1.001:
            cw, ch = w / z, h / z
            img2 = img2.resize((w, h), Image.BICUBIC, box=((w - cw) / 2, (h - ch) / 2, (w + cw) / 2, (h + ch) / 2))
        return img2

    return frame


def render_local_scene(scene: Scene, project_path: Path, settings: Settings, out: Path,
                       character_image: Path | None = None, duration: float | None = None) -> Path:
    kind = scene.local.get("kind")
    dur = duration or scene.duration
    sc = Scene(**{**scene.__dict__, "duration": dur})
    if kind == "card":
        fn = card_frames(sc, settings)
    elif kind == "chart":
        fn = chart_frames(sc, settings)
    elif kind == "screenshot":
        fn = screenshot_frames(Image.open(resolve_image(project_path, settings, scene.local["image"])), sc, settings)
    elif kind == "image":
        fn = kenburns_frames(Image.open(resolve_image(project_path, settings, scene.local["image"])), sc, settings)
    elif kind == "character":
        if not character_image:
            raise LocalRenderError(f"{scene.id}: нет утверждённого референса персонажа")
        fn = kenburns_frames(Image.open(character_image), sc, settings)
    else:
        raise LocalRenderError(f"{scene.id}: неизвестный local.kind '{kind}'")
    return write_frames(out, fn, dur, settings)
