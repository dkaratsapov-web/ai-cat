"""Субтитры: разбиение на короткие фразы, тайминг по озвучке, экспорт ASS (вшивание) и SRT (для площадок)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings


@dataclass
class Cue:
    start: float
    end: float
    text: str
    scene_id: str


def tokenize(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def chunk_words(words: list[str], max_words: int, max_chars: int) -> list[list[int]]:
    """Группирует индексы слов в фразы: не больше max_words и max_chars, разрыв после знаков препинания."""
    groups: list[list[int]] = []
    cur: list[int] = []
    cur_len = 0
    for i, w in enumerate(words):
        add = len(w) + (1 if cur else 0)
        real = sum(1 for k in cur if re.search(r"\w", words[k]))   # тире и знаки — не слова
        if cur and (real >= max_words or cur_len + add > max_chars):
            groups.append(cur)
            cur, cur_len, add = [], 0, len(w)
        cur.append(i)
        cur_len += add
        if re.search(r"[.!?…:;]$", w) or (re.search(r",$", w) and len(cur) >= 2):
            groups.append(cur)
            cur, cur_len = [], 0
    if cur:
        groups.append(cur)
    return groups


def scene_cues(scene_id: str, text: str, start: float, voice_meta: dict | None, fallback_dur: float,
               max_words: int, max_chars: int) -> list[Cue]:
    words = tokenize(text)
    if not words:
        return []
    timings: list[tuple[float, float]] | None = None
    if voice_meta and voice_meta.get("words") and voice_meta.get("text", "").strip() == text.strip():
        vw = voice_meta["words"]
        if len(vw) == len(words):
            timings = [(float(a), float(b)) for _, a, b in vw]
    if timings is None:
        # Пропорционально длине слов в пределах длительности озвучки сцены
        total = (voice_meta or {}).get("duration") or fallback_dur
        lead, tail = 0.1, 0.15
        span = max(total - lead - tail, 0.5)
        weights = [len(w) + 2 for w in words]
        scale = span / sum(weights)
        t = lead
        timings = []
        for wgt in weights:
            timings.append((t, t + wgt * scale))
            t += wgt * scale
    cues = []
    for g in chunk_words(words, max_words, max_chars):
        a = timings[g[0]][0]
        b = timings[g[-1]][1]
        cues.append(Cue(start=round(start + a, 3), end=round(start + max(b, a + 0.35), 3),
                        text=" ".join(words[i] for i in g), scene_id=scene_id))
    # Короткая фраза (одно слово на долю секунды, как «геосервисы» за 0.17 с) не читается — склеиваем с соседней
    min_show = 0.7
    i = 0
    while len(cues) > 1 and i < len(cues):
        c = cues[i]
        if c.end - c.start >= min_show:
            i += 1
            continue
        j = i - 1 if i > 0 else i + 1
        a, b = (cues[j], c) if j < i else (c, cues[j])
        merged = Cue(start=a.start, end=b.end, text=f"{a.text} {b.text}", scene_id=scene_id)
        if len(merged.text) > max_chars * 2 + 4 and j < i and i + 1 < len(cues):   # в 2 строки не влезает — к следующей
            a, b = c, cues[i + 1]
            merged, j = Cue(start=a.start, end=b.end, text=f"{a.text} {b.text}", scene_id=scene_id), i + 1
        lo = min(i, j)
        cues[lo:lo + 2] = [merged]
        i = max(lo - 1, 0)
    # Без «дыр» между соседними фразами внутри сцены: держим фразу до начала следующей
    for i in range(len(cues) - 1):
        if cues[i + 1].start - cues[i].end < 0.4:
            cues[i].end = cues[i + 1].start
    return cues


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def write_ass(cues: list[Cue], path: Path, settings: Settings, font_name: str) -> Path:
    st = settings.get("subtitles")
    w, h = settings.get("video.width"), settings.get("video.height")
    sa = settings.get("safe_area")
    style = (
        f"Style: Main,{font_name},{st['font_size']},{ass_color(st['primary_color'])},{ass_color(st['highlight_color'])},"
        f"{ass_color(st['outline_color'])},{ass_color('#000000', 0x80)},-1,0,0,0,100,100,0,0,1,"
        f"{st['outline']},{st['shadow']},2,{sa['left']},{sa['right']},{st['margin_v']},1"
    )
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 0",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic,"
        " Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL,"
        " MarginR, MarginV, Encoding",
        style, "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for c in cues:
        text = c.text.upper() if st.get("uppercase") else c.text
        text = text.replace("{", "(").replace("}", ")")
        # лёгкое «выпрыгивание» фразы
        anim = r"{\fad(60,40)\t(0,90,\fscx106\fscy106)\t(90,180,\fscx100\fscy100)}"
        lines.append(f"Dialogue: 0,{_ts_ass(c.start)},{_ts_ass(c.end)},Main,,0,0,0,,{anim}{text}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_srt(cues: list[Cue], path: Path) -> Path:
    out = []
    for i, c in enumerate(cues, 1):
        out += [str(i), f"{_ts_srt(c.start)} --> {_ts_srt(c.end)}", c.text, ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def read_srt_text(path: Path) -> str:
    blocks = Path(path).read_text(encoding="utf-8").strip().split("\n\n")
    texts = []
    for b in blocks:
        parts = b.strip().splitlines()
        if len(parts) >= 3:
            texts.append(" ".join(parts[2:]))
    return " ".join(texts)
