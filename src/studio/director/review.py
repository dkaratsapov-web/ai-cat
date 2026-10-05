"""Review-пакет для контент-директора (ChatGPT): всё, что нужно для ревью, в одной папке/архиве."""
from __future__ import annotations

import csv
import json
import shutil
import subprocess
from pathlib import Path

from ..db import DB
from ..editing import ffmpeg
from ..project import Project
from . import qc, state

COST_FIELDS = ["date", "episode", "scene", "provider", "model", "duration", "estimated_cost", "actual_cost", "status",
               "generation_id"]


def cost_rows(db: DB, episode: str | None = None) -> list[dict]:
    rows = []
    with db.conn() as c:
        q = "SELECT * FROM jobs" + (" WHERE episode=?" if episode else "") + " ORDER BY created_at"
        for j in c.execute(q, (episode,) if episode else ()).fetchall():
            j = dict(j)
            params = json.loads(j.get("params_json") or "{}")
            paid = bool(j.get("paid"))
            act = j.get("actual_cost_usd")
            rows.append({"date": (j.get("created_at") or "")[:19], "episode": j["episode"], "scene": j["scene_id"],
                         "provider": j["provider"], "model": j.get("model") or "",
                         "duration": params.get("billed_duration", ""),
                         "estimated_cost": f"{float(j.get('est_cost_usd') or 0) if paid else 0:.3f}",
                         "actual_cost": f"{float(act):.3f}" if act is not None else "",
                         "status": j["status"], "generation_id": j.get("external_task_id") or j["id"]})
    return rows


def write_cost_csv(db: DB, out: Path, episode: str | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:   # -sig: Excel откроет кириллицу
        w = csv.DictWriter(f, fieldnames=COST_FIELDS)
        w.writeheader()
        w.writerows(cost_rows(db, episode))
    return out


def build_review_pack(project: Project, db: DB, *, video: Path | None = None) -> Path:
    """review/ : contact_sheet.jpg, frames/, preview.mp4, review.md, director_notes.md, costs.csv → review_pack.zip"""
    state.sync_from_jobs(project, db)
    out = project.path / "review"
    if out.exists():
        shutil.rmtree(out)
    (out / "frames").mkdir(parents=True)
    video = video or project.final_video
    timeline = []
    tl_path = project.dir("output") / "timeline.json"
    if tl_path.exists():
        timeline = json.loads(tl_path.read_text(encoding="utf-8"))
    script = project.load_script()
    st = state.load(project)
    sheet_imgs, sheet_labels, rows = [], [], []
    for it in timeline:
        sid = it["scene"]
        sc = next((s for s in script.scenes if s.id == sid), None)
        frames = []
        if video.exists():
            for k, frac in enumerate((0.2, 0.5, 0.85)):
                t = it["start"] + it["duration"] * frac
                f = qc.frame_at(video, t, out / "frames" / f"{sid}_{k + 1}_{t:.1f}s.jpg")
                if f:
                    frames.append(f)
        if frames:
            sheet_imgs.append(frames[1] if len(frames) > 1 else frames[0])
            sheet_labels.append(f"{sid} · {it['start']:.1f}s")
        s = st.get(sid, {})
        rows.append(f"| {sid} | {it['start']:.1f}–{it['start'] + it['duration']:.1f} | {(sc.local.get('location') if sc else '') or ''} "
                    f"| {it['generator']} | {s.get('status', '—')} | v{s.get('versions', 0)} "
                    f"| {(s.get('identity_qc') or {}).get('verdict', '—')} | {(sc.subtitle_text if sc else '')[:70]} |")
    if sheet_imgs:
        qc.sheet(sheet_imgs, sheet_labels, out / "contact_sheet.jpg", cols=5, cell_w=240)
    if video.exists():
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vf", "scale=540:-2",
                        "-c:v", "libx264", "-crf", "27", "-preset", "veryfast", "-c:a", "aac", "-b:a", "128k",
                        str(out / "preview.mp4")], check=False)
    notes = project.path / "director" / "director_notes.md"
    if notes.exists():
        shutil.copy2(notes, out / "director_notes.md")
    write_cost_csv(db, out / "costs.csv", project.id)
    total = sum(float(r["actual_cost"] or r["estimated_cost"]) for r in cost_rows(db, project.id))
    dur = ffmpeg.duration(video) if video.exists() else 0
    md = [f"# Review-пакет: {project.id}", "",
          f"Ролик: {dur:.1f} с · сцен: {len(timeline)} · расходы по эпизоду: ${total:.2f}", "",
          "Файлы: `preview.mp4` (540p), `contact_sheet.jpg` (по кадру на сцену), `frames/` (3 кадра на сцену), "
          "`costs.csv`, `director_notes.md`.", "",
          "| Сцена | Время, с | Локация | Генератор | Статус | Версия | QC кота | Текст |",
          "|---|---|---|---|---|---|---|---|", *rows, "",
          "## Что проверить директору",
          "- первые 3 секунды, темп, логику монтажа, переходы, субтитры, CTA, лишние паузы;",
          "- внешность кота в каждой AI-сцене (QC идентичности: pass/fail по сценам);",
          "- «AI-пластик», попадание в нишу.", "",
          "## Формат ответа (вставить владельцу для Claude)",
          "```", "REVIEW FROM CONTENT DIRECTOR:", "- s04: <что не так> → <что сделать>",
          "- общее: <правка для director_notes.md>", "- final: s01, s02, ...  (какие сцены утверждены)", "```"]
    (out / "review.md").write_text("\n".join(md), encoding="utf-8")
    zip_path = shutil.make_archive(str(project.path / f"review_pack_{project.id}"), "zip", out)
    return Path(zip_path)
