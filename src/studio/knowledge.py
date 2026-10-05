"""База знаний и материалов студии — то, на что опираются агенты (Claude-продюсер и директор GPT).

Источники (всё, что уже есть в проекте, плюс папка knowledge/ для новых материалов владельца):
  knowledge/**                  кейсы, заметки, материалы владельца (.md .txt .yaml .csv .json .docx, картинки)
  .claude/product-marketing-context.md   паспорт бренда
  CLAUDE.md, docs/**            правила, контент-план, ТЗ, разборы роликов
  briefs/, assets/templates/scripts/     брифы и образцы сценариев
  projects/*/script, director   сценарии и решения по эпизодам
  assets/character, logos, media, music  утверждённые ассеты (картинки — для просмотра агентом)

Поиск — простой и прозрачный: по словам запроса, без внешних сервисов и без платы.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

TEXT_EXT = {".md", ".txt", ".yaml", ".yml", ".csv", ".json"}
DOC_EXT = {".docx"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}

SOURCES = [   # (glob, раздел)
    ("knowledge/**/*", "материалы владельца"),
    (".claude/product-marketing-context.md", "бренд"),
    ("CLAUDE.md", "правила"),
    ("docs/**/*", "документы"),
    ("briefs/*", "брифы"),
    ("assets/templates/scripts/*", "образцы сценариев"),
    ("projects/*/script/script.yaml", "сценарии эпизодов"),
    ("projects/*/director/*.md", "решения по эпизодам"),
    ("assets/character/character.yaml", "персонаж"),
    ("assets/character/references/*", "кадры кота"),
    ("assets/logos/*", "логотипы"),
    ("assets/templates/media/*", "медиа-шаблоны"),
]
SKIP_PARTS = {".git", "__pycache__", "data", ".venv", "venv"}


@dataclass
class Doc:
    path: str          # от корня проекта, через /
    section: str
    kind: str          # text | image
    title: str
    size: int


def _title(p: Path, text: str | None) -> str:
    if text:
        for line in text.splitlines():
            s = line.strip().lstrip("#").strip().strip('"')
            if s and not s.startswith(("---", "```")):
                return s[:90]
    return p.stem.replace("_", " ")


def read_text(p: Path, limit: int = 200_000) -> str:
    if p.suffix.lower() in DOC_EXT:
        try:
            with zipfile.ZipFile(p) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", "", xml)[:limit]
        except (zipfile.BadZipFile, KeyError):
            return ""
    return p.read_text(encoding="utf-8", errors="replace")[:limit]


def catalog(settings: Settings) -> list[Doc]:
    root = settings.root
    seen: set[Path] = set()
    out: list[Doc] = []
    for pattern, section in SOURCES:
        for p in sorted(root.glob(pattern)):
            if p in seen or not p.is_file() or SKIP_PARTS & set(p.relative_to(root).parts):
                continue
            if p.name.startswith(".env") or p.name in (".gitkeep", "INDEX.md"):
                continue
            ext = p.suffix.lower()
            if ext in TEXT_EXT | DOC_EXT:
                kind, text = "text", read_text(p, 4000)
            elif ext in IMG_EXT:
                kind, text = "image", None
            else:
                continue
            seen.add(p)
            out.append(Doc(p.relative_to(root).as_posix(), section, kind, _title(p, text), p.stat().st_size))
    return out


def _terms(query: str) -> list[str]:
    # грубая «основа» слова: первые 5 букв — чтобы «кейсы»/«кейс»/«кейсов» находились вместе
    return [w[:5] if len(w) > 5 else w for w in re.findall(r"[\wёЁ-]{3,}", query.lower())]


def search(settings: Settings, query: str, limit: int = 8) -> list[dict]:
    terms = _terms(query)
    if not terms:
        return []
    hits = []
    for d in catalog(settings):
        if d.kind != "text":
            if any(t in (d.path + " " + d.title).lower() for t in terms):
                hits.append({"path": d.path, "section": d.section, "score": 1, "snippet": f"[картинка] {d.title}"})
            continue
        text = read_text(settings.root / d.path)
        low = text.lower()
        # каждое слово запроса — не больше 5 очков (длинные файлы не забивают выдачу), все слова сразу — бонус
        found = [min(low.count(t), 5) for t in terms]
        score = sum(found) + 5 * sum(t in d.path.lower() for t in terms) + (10 if all(found) else 0)
        if d.section == "материалы владельца":
            score *= 2
        if not score:
            continue
        lines = text.splitlines()
        best = max(range(len(lines)), key=lambda i: sum(t in lines[i].lower() for t in terms), default=0)
        snippet = "\n".join(lines[max(0, best - 2): best + 4]).strip()
        hits.append({"path": d.path, "section": d.section, "score": score, "snippet": snippet[:700]})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


def catalog_text(settings: Settings, max_chars: int = 6000) -> str:
    lines, cur = [], None
    for d in catalog(settings):
        if d.section != cur:
            cur = d.section
            lines.append(f"\n## {cur}")
        lines.append(f"- {d.path} — {d.title}" + (" (картинка)" if d.kind == "image" else ""))
    text = "\n".join(lines).strip()
    return text if len(text) <= max_chars else text[:max_chars] + "\n…(список обрезан; полный — knowledge/INDEX.md)"


def write_index(settings: Settings) -> Path:
    docs = catalog(settings)
    out = settings.root / "knowledge" / "INDEX.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# База знаний и материалов — оглавление\n\nСобирается командой `studio kb index`. "
                   f"Всего: {sum(d.kind == 'text' for d in docs)} документов, "
                   f"{sum(d.kind == 'image' for d in docs)} картинок.\n\n" + catalog_text(settings, 10**7) + "\n",
                   encoding="utf-8")
    return out
