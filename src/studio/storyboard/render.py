"""Человекочитаемая раскадровка для согласования (терминал + Markdown)."""
from __future__ import annotations

from ..models import CONTENT_TYPES, SCENE_TYPES, Script


def storyboard_markdown(script: Script, warnings: list[str] | None = None, estimate_table: str | None = None) -> str:
    out = [
        f"# {script.title}",
        "",
        f"- **Тип:** {CONTENT_TYPES.get(script.content_type, script.content_type)}",
        f"- **Аудитория:** {script.audience}",
        f"- **Идея:** {script.idea}",
        f"- **Проблема:** {script.problem}",
        f"- **Формат:** {script.format}",
        f"- **Хук (0–3 с):** {script.hook}",
        f"- **Призыв к действию:** {script.cta}",
        f"- **Плановая длительность:** {script.planned_duration} с (цель {script.target_duration} с)",
        f"- **Голос:** {script.voice_preset}",
    ]
    if script.disclaimer:
        out.append(f"- **Дисклеймер:** {script.disclaimer}")
    out += ["", "## Раскадровка", ""]
    t = 0.0
    for s in script.scenes:
        paid = s.generator in ("kling", "hedra", "runway")
        out += [
            f"### {s.id} · {t:.1f}–{t + s.duration:.1f} с · {SCENE_TYPES.get(s.type, s.type)}"
            f" · генератор: `{s.generator}`{' 💲' if paid else ''}",
            "",
            f"**Озвучка:** {s.voiceover or '—'}",
            "",
            f"**Кадр:** {s.visual or '—'}",
        ]
        if s.animation:
            out.append(f"\n**Анимация:** {s.animation}")
        if s.prompts:
            for k, v in s.prompts.items():
                out.append(f"\n**Промпт {k}:** {v}")
        if s.local:
            out.append(f"\n**Локальный рендер:** `{s.local}`")
        if s.assets:
            out.append(f"\n**Нужные материалы:** {', '.join(s.assets)}")
        out.append("")
        t += s.duration
    pub = script.publication or {}
    if pub:
        out += ["## Публикация", ""]
        if pub.get("titles"):
            out.append("**Заголовки:** " + " / ".join(pub["titles"]))
        if pub.get("cover_titles"):
            out.append("\n**Обложка:** " + " / ".join(pub["cover_titles"]))
        if pub.get("description"):
            out.append("\n**Описание:** " + pub["description"])
        if pub.get("hashtags"):
            out.append("\n**Хештеги:** " + " ".join(pub["hashtags"]))
        out.append("")
    if warnings:
        out += ["## Предупреждения", "", *[f"- {w}" for w in warnings], ""]
    if estimate_table:
        out += ["## Смета платных операций", "", "```", estimate_table, "```", ""]
    out += [
        "## Команды согласования",
        "",
        "- Утвердить: `studio script approve <id>`",
        "- Изменить сцену: `studio scene set <id> <scene> --voiceover \"...\" --duration 4 --prompt \"...\"`",
        "- Переписать озвучку / стиль / длительность — попросите Claude Code или отредактируйте script/script.yaml",
        "- Отменить производство: `studio cancel <id>`",
    ]
    return "\n".join(out)
