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


def ease_io(x: float) -> float:
    """Плавный разгон и торможение (ease-in-out) для камеры и переходов."""
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


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


TITLE_SIZE = 80      # основной заголовок (ТЗ: 60–80 px)
NOTE_SIZE = 40       # второстепенная подпись (ТЗ: 38–48 px)


def branded_header(img: Image.Image, settings: Settings, title: str, note: str = "", compact: bool = False) -> int:
    """Единая «шапка» локальных сцен: плашка рубрики, заголовок, жёлтая черта, мелкая подпись.

    Возвращает y, с которого можно рисовать содержимое."""
    w = img.width
    sa = settings.get("safe_area")
    d = ImageDraw.Draw(img)
    rubric_badge(d, settings, sa["left"], sa["top"])
    note_f = fonts.font(settings.fonts_dir, NOTE_SIZE, bold=False)
    text_c = hex_rgb(settings.get("branding.text_color"))
    accent = hex_rgb(settings.get("branding.accent_color"))
    muted = hex_rgb(settings.get("branding.muted_color"))
    max_w = w - 2 * sa["left"]          # справа вверху интерфейс площадки не мешает
    # Не больше двух строк: при длинном заголовке чуть уменьшаем кегль (80 → 72 → 66)
    for size in ((72, 66, 60) if compact else (TITLE_SIZE, 72, 66)):
        title_f = fonts.font(settings.fonts_dir, size)
        lines = wrap(d, title, title_f, max_w)
        if len(lines) <= 2:
            break
    y = sa["top"] + (100 if compact else 130)
    for line in lines:
        d.text((sa["left"], y), line, font=title_f, fill=text_c)
        y += int(size * 1.15)
    y += 16 if compact else 24
    d.rectangle((sa["left"], y, sa["left"] + 160, y + 10), fill=accent)
    y += 10
    if note:
        y += 16 if compact else 22
        d.text((sa["left"], y), note, font=note_f, fill=muted)
        y += int(NOTE_SIZE * 1.3)
    return y + (24 if compact else 40)


def draw_overlays(img: Image.Image, settings: Settings, overlays: list, t: float) -> Image.Image:
    """Надписи поверх видео-сцен (хук, призыв): плашка + текст, плавное появление снизу вверх за 0,4 с.

    overlay: {text, at, size, y, accent: "слово"} — слово из accent выделяется цветом бренда."""
    if not overlays:
        return img
    w = img.width
    sa = settings.get("safe_area")
    accent = hex_rgb(settings.get("branding.accent_color"))
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for ov in overlays:
        if ov.get("style") == "label":   # маркировка рекламы: мелко, но читаемо, весь показ сцены, без анимации
            f = fonts.font(settings.fonts_dir, int(ov.get("size", 34)), bold=False)
            lines = wrap(d, ov["text"], f, w - sa["left"] - sa["right"] - 32)
            y0 = float(ov.get("y", sa["top"] - 120))
            box_w = max(d.textlength(line, font=f) for line in lines) + 32
            d.rounded_rectangle((sa["left"], y0, sa["left"] + box_w, y0 + 44 * len(lines) + 16), radius=12,
                                fill=(14, 16, 22, 190))
            for k, line in enumerate(lines):
                d.text((sa["left"] + 16, y0 + 8 + 44 * k), line, font=f, fill=(255, 255, 255, 235))
            continue
        at = float(ov.get("at", 0.0))
        until = ov.get("until")
        if t < at or (until is not None and t > float(until) + 0.3):
            continue
        p = ease_out((t - at) / float(ov.get("fade", 0.4)))
        if until is not None and t > float(until):
            p = min(p, 1 - ease_io((t - float(until)) / 0.3))
        size = int(ov.get("size", TITLE_SIZE))
        f = fonts.font(settings.fonts_dir, size)
        logo = _logo(settings, ov.get("image"), int(size * 1.25)) if ov.get("image") else None
        logo_w = (logo.width + 20) if logo else 0
        max_w = w - sa["left"] - sa["right"] - 56 - logo_w
        lines = wrap(d, ov["text"], f, max_w) if ov.get("text") else [""]
        lh = int(size * 1.18)
        text_w = max(d.textlength(line, font=f) for line in lines)
        box_w = text_w + 56 + logo_w
        box_h = max(lh * len(lines), logo.height if logo else 0) + 36
        x0 = (w - box_w) / 2
        y0 = float(ov.get("y", sa["top"] + 40)) + (1 - p) * 30
        a = int(255 * p)
        d.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), radius=28, fill=(14, 16, 22, int(205 * p)))
        if logo:   # значок платформы слева от текста, с той же прозрачностью
            lg = logo.copy()
            lg.putalpha(lg.getchannel("A").point(lambda v, k=p: int(v * k)))
            layer.alpha_composite(lg, (int(x0 + 28), int(y0 + (box_h - lg.height) / 2)))
        hl = str(ov.get("accent", "")).lower()
        yy = y0 + 16 + max(0, ((logo.height if logo else 0) - lh * len(lines)) / 2)
        for line in lines:
            xx = x0 + 28 + logo_w + (text_w - d.textlength(line, font=f)) / 2
            for word in line.split(" "):
                plain = word.strip("«»\"'.,!?:;").lower()
                color = accent if hl and plain and plain in hl.split() else (255, 255, 255)
                d.text((xx, yy), word, font=f, fill=(*color, a))
                xx += d.textlength(word + " ", font=f)
            yy += lh
    base = img.convert("RGBA")
    base.alpha_composite(layer)
    return base.convert("RGB")


_LOGOS: dict = {}


def _logo(settings: Settings, name: str, size: int) -> Image.Image | None:
    """Логотип платформы из assets/logos (только оттуда), скруглённый квадрат size×size."""
    key = (name, size)
    if key not in _LOGOS:
        base = (settings.assets_dir / "logos").resolve()
        p = (base / f"{name}.png").resolve()
        if not p.is_relative_to(base) or not p.is_file():
            print(f"    ! логотип {name} не найден в assets/logos")
            _LOGOS[key] = None
        else:
            im = Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS)
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 4, fill=255)
            im.putalpha(Image.composite(im.getchannel("A"), Image.new("L", (size, size), 0), mask))
            _LOGOS[key] = im
    return _LOGOS[key]


def resolve_image(project_path: Path, settings: Settings, rel: str) -> Path:
    """Картинка сцены: только внутри imports/ и images/ проекта или assets/ — и только настоящее изображение.

    Защита от отправки в платный API произвольного файла (например, .env) под видом кадра."""
    # Свои файлы проекта важнее; затем общие материалы шаблонов (assets/templates/media) и assets/
    bases = [project_path / "imports", project_path / "images", settings.assets_dir / "templates" / "media",
             settings.assets_dir]
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
    """local: {kind: card, title, bullets: [..], footnote, bullet_at: [сек..], reveal: stack|single}

    reveal=single — на экране один тезис: следующий плавно сменяет предыдущий (время — bullet_at)."""
    w, h = settings.get("video.width"), settings.get("video.height")
    sa = settings.get("safe_area")
    cfg = scene.local
    bg = background(settings, w, h)
    y = branded_header(bg, settings, cfg.get("title", ""), cfg.get("note", ""))
    d = ImageDraw.Draw(bg)
    bullet_f = fonts.font(settings.fonts_dir, int(cfg.get("bullet_size", 60)))
    note_f = fonts.font(settings.fonts_dir, NOTE_SIZE, bold=False)
    text_c = hex_rgb(settings.get("branding.text_color"))
    accent = hex_rgb(settings.get("branding.accent_color"))
    muted = hex_rgb(settings.get("branding.muted_color"))
    max_w = w - sa["left"] - sa["right"]
    y += 20
    base = bg.copy()
    bullets = cfg.get("bullets", []) or []
    single = cfg.get("reveal") == "single"
    layout = []
    for b in bullets:
        lines = wrap(d, b, bullet_f, max_w - 90)
        layout.append((y, lines))
        if not single:
            y += int(bullet_f.size * 1.2) * len(lines) + 40
    if cfg.get("footnote"):
        d2 = ImageDraw.Draw(base)
        fy = h - sa["bottom"] - 200   # не у нижней границы: там субтитры и интерфейс площадки
        for line in wrap(d2, cfg["footnote"], note_f, max_w):
            d2.text((sa["left"], fy), line, font=note_f, fill=muted)
            fy += 52
    reveal = float(cfg.get("reveal_every", max(0.4, (scene.duration - 0.6) / max(len(bullets), 1))))
    times = [float(x) for x in cfg.get("bullet_at", [])] or [0.3 + i * reveal for i in range(len(bullets))]
    times += [times[-1] + reveal * (k + 1) for k in range(len(bullets) - len(times))] if times else []

    def frame(t: float) -> Image.Image:
        img = base.copy()
        dd = ImageDraw.Draw(img, "RGBA")
        for i, (by, lines) in enumerate(layout):
            p = ease_out((t - times[i]) / 0.35)
            if single and i + 1 < len(times) and t >= times[i + 1]:
                p = min(p, 1 - ease_io((t - times[i + 1]) / 0.25))   # уходит, когда появляется следующий
            if p <= 0:
                continue
            a = int(255 * p)
            dx = int((1 - p) * 40)
            dd.ellipse((sa["left"] + dx, by + 12, sa["left"] + 40 + dx, by + 52), fill=(*accent, a))
            ly = by
            for line in lines:
                dd.text((sa["left"] + 76 + dx, ly), line, font=bullet_f, fill=(*text_c, a))
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


def to_px(box, width: int, height: int) -> list[int]:
    """[x, y, w, h] в пикселях или в долях (все значения ≤ 1) → пиксели. Доли не зависят от разрешения скриншота."""
    x, y, bw, bh = [float(v) for v in box]
    if max(x, y, bw, bh) <= 1.0:
        return [round(x * width), round(y * height), round(bw * width), round(bh * height)]
    return [round(x), round(y), round(bw), round(bh)]


def redact_boxes(img: Image.Image, boxes: list) -> Image.Image:
    """Обезличивание: сильное размытие прямоугольников [x, y, w, h] (в пикселях исходного скриншота)."""
    if not boxes:
        return img
    out = img.copy()
    for b in boxes:
        x, y, bw, bh = to_px(b, img.width, img.height)
        region = out.crop((x, y, x + bw, y + bh))
        # пикселизация + размытие — текст не восстанавливается
        small = region.resize((max(1, bw // 16), max(1, bh // 16)), Image.BILINEAR)
        region = small.resize((bw, bh), Image.NEAREST).filter(ImageFilter.GaussianBlur(6))
        out.paste(region, (x, y))
    return out


HL_COLORS = {"accent": None, "red": (235, 64, 64)}


def screenshot_frames(img: Image.Image, scene: Scene, settings: Settings) -> Frame:
    """local: {kind: screenshot, image, caption, note, crop: [x,y,w,h], redact: [[x,y,w,h]],
               highlights: [{box, at, until, color: accent|red, zoom: 1.3, focus: [x,y,w,h], label, label_pos}]}

    Единый стиль с карточками: фон бренда, плашка рубрики, заголовок (caption) с жёлтой чертой, ниже — интерфейс.
    Камера плавно (ease-in-out, 0,45 с) приближается к области подсветки: focus — показать область целиком,
    zoom — приблизить в N раз относительно общего плана. Координаты — пиксели исходника или доли 0–1.
    """
    w, h = settings.get("video.width"), settings.get("video.height")
    sa = settings.get("safe_area")
    cfg = scene.local
    accent = hex_rgb(settings.get("branding.accent_color"))
    src = redact_boxes(img.convert("RGB"), cfg.get("redact", []))
    src_w, src_h = src.size
    ox, oy, cw, ch = (to_px(cfg["crop"], src_w, src_h) if cfg.get("crop") else [0, 0, src_w, src_h])
    base = background(settings, w, h)
    area_top = branded_header(base, settings, cfg.get("caption", ""), cfg.get("note", ""), compact=True)
    # Низ панели — выше строки субтитров (до двух строк), чтобы текст не наезжал на интерфейс
    st = settings.get("subtitles") or {}
    area_bottom = h - int(st.get("margin_v", sa["bottom"])) - int(int(st.get("font_size", 54)) * 2.6) - 20
    margin = 40
    ax0, ax1 = margin, w - margin
    avail_w, avail_h = ax1 - ax0, area_bottom - area_top
    lab_f = fonts.font(settings.fonts_dir, 44)

    def fit(rect, pad: float = 1.0) -> tuple[float, float, float]:
        """Камера (масштаб, центр x, центр y в пикселях исходника), чтобы rect поместился в область."""
        rx, ry, rw, rh = rect
        sc = min(avail_w / rw, avail_h / rh) / pad
        return sc, rx + rw / 2, ry + rh / 2

    overview = fit((ox, oy, cw, ch))
    highlights = [dict(hl) for hl in (cfg.get("highlights", []) or [])]
    keys = [(0.0, overview)]
    for hl in sorted(highlights, key=lambda x: float(x.get("at", 0.5))):
        if hl.get("focus") or hl.get("zoom"):
            bx, by, bw, bh = to_px(hl.get("focus") or hl["box"], src_w, src_h)
            if hl.get("focus"):
                cam = fit((bx, by, bw, bh), 1.04)
                cam = (min(cam[0], overview[0] * float(hl.get("max_zoom", 2.2))), cam[1], cam[2])
            else:
                cam = (overview[0] * float(hl["zoom"]), bx + bw / 2, by + bh / 2)
            keys.append((max(0.0, float(hl.get("at", 0.5)) - 0.15), cam))
    move = float(cfg.get("camera_move", 0.45))
    view_cx0, view_cy0 = (ax0 + ax1) / 2, (area_top + area_bottom) / 2

    def camera(t: float) -> tuple[float, float, float]:
        cur = keys[0][1]
        for k_at, k_cam in keys[1:]:
            if t <= k_at:
                break
            p = ease_io((t - k_at) / move)
            cur = (math.exp(math.log(cur[0]) + (math.log(k_cam[0]) - math.log(cur[0])) * p),
                   cur[1] + (k_cam[1] - cur[1]) * p, cur[2] + (k_cam[2] - cur[2]) * p)
        return cur

    def clamp_center(sc: float, cx: float, cy: float) -> tuple[float, float]:
        """Не показывать пустоту за краями кадрированной области, если она больше окна."""
        half_w, half_h = avail_w / 2 / sc, avail_h / 2 / sc
        if cw / 2 > half_w:
            cx = min(max(cx, ox + half_w), ox + cw - half_w)
        else:
            cx = ox + cw / 2
        if ch / 2 > half_h:
            cy = min(max(cy, oy + half_h), oy + ch - half_h)
        else:
            cy = oy + ch / 2
        return cx, cy

    dur = max(scene.duration, 0.1)
    drift = float(cfg.get("zoom_to", 1.03)) - 1   # лёгкое общее «дыхание» камеры за сцену

    def frame(t: float) -> Image.Image:
        sc, cx, cy = camera(t)
        sc *= 1 + drift * ease_io(t / dur)
        cx, cy = clamp_center(sc, cx, cy)
        # видимая часть исходника → панель на экране
        vx0, vy0 = max(ox, cx - avail_w / 2 / sc), max(oy, cy - avail_h / 2 / sc)
        vx1, vy1 = min(ox + cw, cx + avail_w / 2 / sc), min(oy + ch, cy + avail_h / 2 / sc)
        px0, py0 = view_cx0 + (vx0 - cx) * sc, view_cy0 + (vy0 - cy) * sc
        pw, ph = max(1, round((vx1 - vx0) * sc)), max(1, round((vy1 - vy0) * sc))
        out = base.copy()
        sh = Image.new("RGBA", (pw + 60, ph + 60), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((30, 30, pw + 30, ph + 30), radius=22, fill=(0, 0, 0, 150))
        sh = sh.filter(ImageFilter.GaussianBlur(14))
        out.paste(sh, (round(px0) - 30, round(py0) - 18), sh)
        panel = src.resize((pw, ph), Image.BICUBIC, box=(vx0, vy0, vx1, vy1))
        mask = Image.new("L", (pw, ph), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw - 1, ph - 1), radius=22, fill=255)
        out.paste(panel, (round(px0), round(py0)), mask)
        hl_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dd = ImageDraw.Draw(hl_layer)
        for hl in highlights:
            at = float(hl.get("at", 0.5))
            until = hl.get("until")
            if t < at or (until is not None and t > float(until) + 0.3):
                continue
            p = ease_out((t - at) / 0.3)
            if until is not None and t > float(until):
                p = min(p, 1 - ease_io((t - float(until)) / 0.3))
            color = HL_COLORS.get(hl.get("color", "accent")) or accent
            fill_a = 70 if hl.get("color") == "red" else 46
            bx, by, bw, bh = to_px(hl["box"], src_w, src_h)
            x0, y0 = view_cx0 + (bx - cx) * sc, view_cy0 + (by - cy) * sc
            x1, y1 = x0 + bw * sc, y0 + bh * sc
            dd.rounded_rectangle((x0 - 6, y0 - 6, x1 + 6, y1 + 6), radius=14,
                                 outline=(*color, int(255 * p)), width=6, fill=(*color, int(fill_a * p)))
            if hl.get("label"):
                lx, ly = x0, (y0 - 20 - 64 - 6) if hl.get("label_pos") == "above" else y1 + 20
                tw = dd.textlength(hl["label"], font=lab_f)
                lx = min(max(lx, sa["left"]), w - tw - 60)
                dd.rounded_rectangle((lx - 16, ly, lx + tw + 16, ly + 64), radius=14, fill=(*color, int(240 * p)))
                dd.text((lx, ly + 8), hl["label"], font=lab_f, fill=(20, 20, 20, int(255 * p)))
        # рамки — только в пределах панели (не вылезают на фон)
        clip = Image.new("L", (w, h), 0)
        ImageDraw.Draw(clip).rounded_rectangle((px0 - 8, py0 - 8, px0 + pw + 8, py0 + ph + 8), radius=26, fill=255)
        hl_layer.putalpha(Image.composite(hl_layer.getchannel("A"), Image.new("L", (w, h), 0), clip))
        out = Image.alpha_composite(out.convert("RGBA"), hl_layer).convert("RGB")
        # шапка поверх панели (панель при приближении не залезает на заголовок)
        out.paste(base.crop((0, 0, w, area_top - 10)), (0, 0))
        return out

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
