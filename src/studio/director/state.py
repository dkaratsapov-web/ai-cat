"""Статусы сцен: draft → approved → generating → generated → review → revise → final.

Хранятся в projects/<id>/director/status.json (вместе с историей и QC идентичности).
generating/generated выводятся из журнала задач автоматически (sync_from_jobs).
final — только после ревью и пройденного QC идентичности для сцен с котом.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..db import ACTIVE_STATUSES, DB
from ..project import Project

STATUSES = ("draft", "approved", "generating", "generated", "review", "manual_review", "revise", "final")
ALLOWED = {
    "draft": {"approved"},
    "approved": {"generating", "draft", "generated"},
    "generating": {"generated", "approved"},
    "generated": {"review", "manual_review", "revise", "final"},
    "review": {"revise", "final", "manual_review"},
    # Директор (OpenAI) недоступен: пакет сохранён, ревью вручную через ChatGPT, проект не блокируется
    "manual_review": {"review", "revise", "final"},
    "revise": {"approved", "generating", "generated", "review", "manual_review"},
    "final": {"revise"},
}


class StateError(ValueError):
    pass


def _path(project: Project) -> Path:
    return project.path / "director" / "status.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(project: Project) -> dict:
    p = _path(project)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save(project: Project, data: dict) -> None:
    p = _path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def init_states(project: Project, scene_ids: list[str], *, approved: bool) -> dict:
    data = load(project)
    for sid in scene_ids:
        data.setdefault(sid, {"status": "approved" if approved else "draft", "history": [], "identity_qc": None,
                              "versions": 0})
    save(project, data)
    return data


def needs_identity_qc(scene) -> bool:
    return scene.generator != "local" and (scene.reference is not None or scene.needs_character)


def set_status(project: Project, sid: str, status: str, note: str = "", *, force: bool = False,
               override: bool = False) -> dict:
    """override — ручное решение пользователя: final вопреки вердикту директора / QC (записывается в историю)."""
    if status not in STATUSES:
        raise StateError(f"Статус {status!r}: допустимо {', '.join(STATUSES)}")
    data = load(project)
    st = data.setdefault(sid, {"status": "draft", "history": [], "identity_qc": None, "versions": 0})
    cur = st["status"]
    if status == cur:
        return st
    if not force and status not in ALLOWED.get(cur, set()):
        raise StateError(f"{sid}: переход {cur} → {status} не разрешён (можно: {', '.join(sorted(ALLOWED[cur]))})")
    if status == "final" and not override:
        script = project.load_script()
        scene = next((s for s in script.scenes if s.id == sid), None)
        director = st.get("director") or {}
        if director.get("status") == "revise" and director.get("version") == st.get("versions"):
            raise StateError(f"{sid}: директор вернул revise для этой версии — в final нельзя "
                             f"(ручное решение: studio scene-status {project.id} {sid} --set final --override)")
        if scene and needs_identity_qc(scene):
            qc = st.get("identity_qc") or {}
            if qc.get("verdict") != "pass":
                raise StateError(f"{sid}: в final нельзя — QC идентичности кота не пройден "
                                 f"(studio idqc {project.id} {sid} --pass после проверки)")
            if qc.get("version") != st.get("versions"):
                raise StateError(f"{sid}: QC идентичности был для версии v{qc.get('version')}, а сейчас v{st.get('versions')} — "
                                 "проверьте новую версию")
    st["history"].append({"from": cur, "to": status, "at": _now(),
                          "note": (note + " [override пользователя]") if override else note})
    st["status"] = status
    save(project, data)
    return st


def sync_from_jobs(project: Project, db: DB) -> dict:
    """generating/generated — по журналу задач; новая версия файла сбрасывает QC и final."""
    data = load(project)
    if not data:
        return data
    script = project.load_script()
    for sc in script.scenes:
        st = data.setdefault(sc.id, {"status": "approved", "history": [], "identity_qc": None, "versions": 0})
        if sc.generator == "local":
            continue
        jobs = [j for j in db.jobs_for(project.id, sc.id) if j["kind"] != "tts"]
        done = [j for j in jobs if j["status"] in ("succeeded", "imported")]
        active = [j for j in jobs if j["status"] in ACTIVE_STATUSES]
        vdir = project.dir("scenes") / sc.id
        versions = len(list(vdir.glob("generation_v*.mp4"))) if vdir.is_dir() else 0
        versions = max(versions, len(done), 1 if project.scene_source(sc.id) else 0)
        if versions > st.get("versions", 0):
            prev = st["status"]
            st["versions"] = versions
            st["identity_qc"] = None   # новая версия — новый QC
            st["status"] = "generated"
            st["history"].append({"from": prev, "to": "generated", "at": _now(), "note": f"версия v{versions}"})
        elif active and st["status"] in ("approved", "revise"):
            st["history"].append({"from": st["status"], "to": "generating", "at": _now(), "note": ""})
            st["status"] = "generating"
    save(project, data)
    return data


def all_final(project: Project) -> list[str]:
    """Сцены, мешающие финальному монтажу (не в final)."""
    data = load(project)
    script = project.load_script()
    return [s.id for s in script.scenes if s.generator != "local" and (data.get(s.id) or {}).get("status") != "final"]
