"""Пакет публикации: обложка, заголовок, описание, хештеги, видеофайл. Публикация — только вручную."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

from ..editing import ffmpeg
from ..editing.fonts import font
from ..editing.local_scenes import hex_rgb, wrap
from ..project import Project

PLATFORMS = {
    "instagram_reels": {"title_max": 0, "desc_max": 2200, "hashtags_max": 30},
    "vk_clips": {"title_max": 100, "desc_max": 2000, "hashtags_max": 10},
    "youtube_shorts": {"title_max": 100, "desc_max": 5000, "hashtags_max": 15},
}


def make_cover(project: Project, title: str, at: float, out: Path) -> Path:
    s = project.settings
    frame = project.dir("work") / "cover_frame.png"
    clean = project.dir("work") / "video.mp4"  # видеоряд без вшитых субтитров
    src = clean if clean.exists() else project.final_video
    ffmpeg.run(["ffmpeg", "-ss", f"{at:.2f}", "-i", src, "-frames:v", "1", frame])
    img = Image.open(frame).convert("RGB")
    w, h = img.size
    sa = s.get("safe_area")
    d = ImageDraw.Draw(img, "RGBA")
    # затемнение верхней трети для читаемости
    for y in range(int(h * 0.45)):
        a = int(170 * (1 - y / (h * 0.45)))
        d.line((0, y, w, y), fill=(0, 0, 0, a))
    f = font(s.fonts_dir, 104)
    accent = hex_rgb(s.get("branding.accent_color"))
    y = sa["top"] + 40
    for i, line in enumerate(wrap(d, title.upper(), f, w - sa["left"] - sa["right"])):
        color = accent if i == 0 else (255, 255, 255)
        d.text((sa["left"], y), line, font=f, fill=color, stroke_width=6, stroke_fill=(0, 0, 0))
        y += 118
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=92)
    return out


def package(project: Project) -> Path:
    script = project.load_script()
    if not project.final_video.exists():
        raise RuntimeError(f"Нет итогового ролика. Выполните: studio assemble {project.id}")
    pub = script.publication or {}
    pdir = project.dir("publish")
    cover_titles = pub.get("cover_titles") or [script.title]
    at = float(pub.get("cover_time", 1.0))
    covers = [make_cover(project, t, at, pdir / f"cover_{i + 1}.jpg") for i, t in enumerate(cover_titles[:3])]
    video = pdir / f"{project.id}.mp4"
    shutil.copy2(project.final_video, video)
    srt = project.dir("subtitles") / f"{project.id}.srt"
    if srt.exists():
        shutil.copy2(srt, pdir / srt.name)

    hashtags = [h if h.startswith("#") else f"#{h}" for h in pub.get("hashtags", [])]
    titles = pub.get("titles") or [script.title]
    desc = (pub.get("description") or "").strip()
    cta = script.cta.strip()
    per_platform = {}
    for name, lim in PLATFORMS.items():
        tags = hashtags[: lim["hashtags_max"]]
        body = "\n\n".join(x for x in (desc, cta, " ".join(tags)) if x)
        per_platform[name] = {
            "title": titles[0][: lim["title_max"]] if lim["title_max"] else "",
            "description": body[: lim["desc_max"]],
            "warnings": [f"описание обрезано до {lim['desc_max']} символов"] if len(body) > lim["desc_max"] else [],
        }
    meta = {
        "episode": project.id, "video": video.name, "covers": [c.name for c in covers], "titles": titles,
        "description": desc, "cta": cta, "hashtags": hashtags, "platforms": per_platform,
        "published": False, "note": "Публикация выполняется только после подтверждения владельца.",
    }
    (pdir / "publish.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# {titles[0]}", "", f"Видео: `{video.name}`", f"Обложки: {', '.join(c.name for c in covers)}", "",
          "## Варианты заголовка", *[f"- {t}" for t in titles], "", "## Описание", desc, "", cta, "",
          "## Хештеги", " ".join(hashtags), ""]
    for name, d in per_platform.items():
        md += [f"## {name}", f"Заголовок: {d['title'] or '—'}", "", d["description"], ""]
    (pdir / "publish.md").write_text("\n".join(md), encoding="utf-8")
    project.set_status("packaged")
    return pdir
