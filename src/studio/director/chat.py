"""Лента эпизода в виде чата: Владелец · Директор (GPT) · Claude (продюсер).

Собирается из того, что уже записано на диск (бриф, статусы сцен, QC, ответы директора, задачи генерации)
плюс свободные вопросы владельца директору (`studio director ask`). Результат — `director/chat.html`,
открывается в браузере и обновляется сам каждые 15 секунд.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from ..db import DB
from ..project import Project
from . import state

ROLES = {
    "owner": ("Владелец", "owner"),
    "director": ("Директор · GPT", "director"),
    "claude": ("Claude · продюсер", "claude"),
}


def _ts(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    for fmt in (None, "%Y%m%d-%H%M%S"):
        try:
            d = datetime.fromisoformat(value) if fmt is None else datetime.strptime(value, fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.min.replace(tzinfo=timezone.utc)


def chat_log(project: Project) -> Path:
    return project.path / "director" / "chat.jsonl"


def append(project: Project, role: str, text: str, **extra) -> None:
    p = chat_log(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"role": role, "text": text, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _review_text(kind: str, scene: str, r: dict) -> str:
    out = []
    if kind == "script_review":
        out.append(f"Ревью сценария: {r.get('status')}")
        if r.get("overall_notes"):
            out.append(r["overall_notes"])
        out += [f"• {n.get('scene_id')}: {n.get('status')} — {n.get('notes')}" for n in r.get("scene_notes") or []
                if n.get("status") != "approved"]
    elif kind == "scene_review":
        idn = r.get("identity") or {}
        out.append(f"Ревью сцены {r.get('scene_id') or scene}: {r.get('status')}")
        out.append(f"Кот: {'ок' if idn.get('pass') else 'НЕ ок'} (уверенность {idn.get('confidence', '?')}) — {idn.get('notes', '')}")
        for k, name in (("motion", "Движение"), ("composition", "Композиция")):
            if r.get(k):
                out.append(f"{name}: {'ок' if r[k].get('pass') else 'правка'} — {r[k].get('notes', '')}")
        out += [f"• {i}" for i in r.get("issues") or []]
        if r.get("revision_prompt"):
            out.append(f"Предлагаемый промпт: {r['revision_prompt']}")
    else:
        out.append(f"Финальное ревью: {r.get('status')}, оценка {r.get('final_score')}")
        out += [f"• {c.get('timestamp')} {c.get('scene_id')}: {c.get('change')}" for c in r.get("required_changes") or []]
        opt = r.get("optional_improvements") or []
        if opt:
            out.append("Необязательно:")
            out += [f"• {c.get('timestamp')} {c.get('scene_id')}: {c.get('change')}" for c in opt]
    need = r.get("requires_user_approval") or []
    if need:
        out.append("Нужно ваше решение: " + "; ".join(need))
    return "\n".join(out)


def collect(project: Project, db: DB | None = None) -> list[dict]:
    msgs: list[dict] = []
    d = project.path / "director"
    lock = d / "lock.json"
    if lock.exists():
        lk = json.loads(lock.read_text(encoding="utf-8"))
        msgs.append({"role": "owner", "at": lk.get("imported_at"),
                     "text": f"Утвердил бриф «{lk.get('project')}» v{lk.get('version')}. "
                             f"Не менять: {', '.join(lk.get('locked') or []) or '—'}."})
    for sid, st in state.load(project).items():
        for h in st.get("history") or []:
            note = h.get("note") or ""
            role = "owner" if ("override" in note or "пользовател" in note) else "claude"
            msgs.append({"role": role, "at": h.get("at"), "scene": sid,
                         "text": f"{sid}: {h.get('from')} → {h.get('to')}" + (f" · {note}" if note else "")})
        qc = st.get("identity_qc") or {}
        if qc and qc.get("reviewer") != "director":
            msgs.append({"role": "owner", "at": qc.get("at"), "scene": sid,
                         "text": f"Проверка кота {sid} v{qc.get('version')}: {qc.get('verdict')}"
                                 + (f" — {qc['notes']}" if qc.get("notes") else "")})
    rdir = project.settings.root / "director_bridge" / "responses" / project.id
    for f in sorted(rdir.glob("*.json")) if rdir.exists() else []:
        rec = json.loads(f.read_text(encoding="utf-8"))
        tag = " (заглушка)" if rec.get("mode") == "mock" else ""
        msgs.append({"role": "director", "at": rec.get("at"), "scene": rec.get("scene"),
                     "text": _review_text(rec.get("kind", ""), rec.get("scene", ""), rec.get("review") or {}) + tag})
    if db is not None:
        # Задачи одного вида, завершённые в одну минуту, — одним сообщением (иначе озвучка засыпает ленту)
        groups: dict[tuple, list[dict]] = {}
        for j in db.jobs_for(project.id):
            if j["status"] not in ("succeeded", "failed"):
                continue
            key = (j.get("kind"), j["provider"], j.get("model") or "", j["status"], (j.get("updated_at") or "")[:16])
            groups.setdefault(key, []).append(j)
        for (kind, prov, model, status, _m), jobs in groups.items():
            what = {"tts": "Озвучка", "import": "Импорт сцены"}.get(kind, "Генерация видео")
            word = "готова" if status == "succeeded" else "ОШИБКА"
            parts, total = [], 0.0
            for j in jobs:
                cost = float(j.get("actual_cost_usd") or j.get("est_cost_usd") or 0) if j.get("paid") else 0.0
                total += cost
                parts.append(j["scene_id"] + (f" ${cost:.2f}" if cost else ""))
            text = f"{what} {word} · {prov} {model}\n" + ", ".join(parts)
            if total:
                text += f"\nИтого: ${total:.2f}"
            msgs.append({"role": "claude", "at": jobs[-1].get("updated_at"),
                         "scene": jobs[0]["scene_id"] if len(jobs) == 1 else "", "text": text})
    log = chat_log(project)
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                msgs.append(json.loads(line))
    msgs.sort(key=lambda m: _ts(m.get("at")))
    return msgs


CSS = """
:root{--bg:#f4f5f7;--fg:#1d2129;--muted:#6b7280;--card:#fff;--owner:#dbeafe;--director:#dcfce7;--claude:#ede9fe;--line:#e5e7eb}
@media (prefers-color-scheme:dark){:root{--bg:#14161b;--fg:#e8eaee;--muted:#9aa1ad;--card:#1d2027;--owner:#1e3a5f;--director:#173d2a;--claude:#2e2650;--line:#2b2f38}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px}
h1{font-size:17px;margin:0 0 6px}.legend span{display:inline-block;margin-right:10px;font-size:13px;color:var(--muted)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:4px;vertical-align:middle}
main{max-width:820px;margin:0 auto;padding:12px 16px 40px}
.msg{max-width:88%;margin:10px 0;padding:9px 12px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}
.owner{background:var(--owner);margin-left:auto;border-bottom-right-radius:3px}
.director{background:var(--director);border-bottom-left-radius:3px}
.claude{background:var(--claude);border-bottom-left-radius:3px}
.meta{font-size:12px;color:var(--muted);margin-bottom:3px}.tools{font-size:13px;color:var(--muted)}
input{margin-top:8px;width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}
"""


def render(project: Project, db: DB | None = None) -> Path:
    msgs = collect(project, db)
    rows = []
    for m in msgs:
        name, cls = ROLES.get(m.get("role"), ROLES["claude"])
        t = _ts(m.get("at"))
        when = t.astimezone().strftime("%d.%m %H:%M") if t.year > 1 else ""
        sc = m.get("scene") or ""
        rows.append(f'<div class="msg {cls}" data-scene="{html.escape(sc)}"><div class="meta">{name} · {when}</div>'
                    f"{html.escape(m.get('text', ''))}</div>")
    page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="15">
<title>Чат эпизода</title><style>{CSS}</style></head><body>
<header><h1>{html.escape(project.id)}</h1>
<div class="legend"><span><i style="background:var(--owner)"></i>Владелец</span><span><i style="background:var(--director)"></i>Директор · GPT</span><span><i style="background:var(--claude)"></i>Claude · продюсер</span></div>
<div class="tools">Вопрос директору: <code>studio director ask {html.escape(project.id)} "текст"</code> · страница обновляется сама</div>
<input id="f" placeholder="Фильтр по сцене, например s04" oninput="for(const e of document.querySelectorAll('.msg'))e.style.display=!this.value||e.dataset.scene===this.value.trim()?'':'none'">
</header><main>{''.join(rows) or '<p class="tools">Пока пусто.</p>'}</main>
<script>window.scrollTo(0,document.body.scrollHeight)</script></body></html>"""
    out = project.path / "director" / "chat.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out
