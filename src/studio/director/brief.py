"""Director Brief: утверждённый ChatGPT бриф → проект студии. Claude исполняет, не сочиняет.

Формат (YAML):
  project: fitness_top5
  status: approved                 # импорт только approved
  version: 1
  title: "..."
  locked: [character, location, text, cta, composition]   # что нельзя менять без нового брифа
  goal: {audience, objective, cta}
  voice: {preset: default}
  review_rules: {preserve_face: true, preserve_folded_ears: true, no_extra_limbs: true, no_cartoon_style: true}
  scenes:
    - id: s01
      location: office | fitness_studio
      asset: closeup_front           # id из approved_assets (studio assets)
      type: talking | motion | local
      generator: kling | higgsfield | local
      model: ...                     # необязательно (для higgsfield)
      duration: 4
      voiceover: "текст для синтеза"
      subtitle: "текст на экране"     # если отличается
      motion: "action prompt in English"   # без описания внешности
      overlays: [...]                # плашки (composition)
      transition: zoomin | smoothleft | ...
      locked: [...]                  # необязательно: свои locked-поля сцены
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..config import Settings
from ..models import Scene, Script
from ..project import Project, ProjectError, create_project
from .assets import approved_assets

LOCK_FIELDS = ("character", "location", "text", "cta", "composition")
REQUIRED_SUFFIX = "ears stay folded, static camera"


class BriefError(ValueError):
    pass


def load_brief(path: Path) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise BriefError(f"Бриф не читается как YAML: {e}") from e
    if not isinstance(data, dict):
        raise BriefError("Бриф должен быть словарём YAML")
    return data


def _asset_id(value: str | None, assets: dict) -> str | None:
    """asset можно указать id («fit_logo») или путём («fitness/fit_logo.png») — сопоставляем по имени файла."""
    if not value:
        return value
    if value in assets:
        return value
    stem = Path(str(value)).stem
    return stem if stem in assets else value


def normalize_brief(brief: dict, settings: Settings) -> dict:
    """Приводит формы записи к одной: locked_* флаги → locked-списки, путь ассета → id."""
    b = dict(brief)
    glob = set(b.get("locked") or [])
    ch, st = b.get("character") or {}, b.get("style") or {}
    if ch.get("locked_character") or ch.get("locked_outfit"):
        glob.add("character")
    if st.get("locked_style"):
        glob.add("composition")
    if (b.get("goal") or {}).get("cta") and b.get("locked_cta", True):
        glob.add("cta")
    b["locked"] = sorted(glob)
    assets = approved_assets(settings)
    scenes = []
    for sc in b.get("scenes") or []:
        sc = dict(sc)
        sc["asset"] = _asset_id(sc.get("asset"), assets)
        locks = set(sc.get("locked") or [])
        for f in LOCK_FIELDS:
            if sc.pop(f"locked_{f}", False):
                locks.add(f)
        sc["locked"] = sorted(locks)
        scenes.append(sc)
    b["scenes"] = scenes
    return b


def validate_brief(brief: dict, settings: Settings) -> list[str]:
    """Ошибки брифа. Пустой список — можно импортировать."""
    errs: list[str] = []
    if brief.get("status") != "approved":
        errs.append(f"status: {brief.get('status')!r} — импортируются только брифы со status: approved")
    if not brief.get("project"):
        errs.append("нет project")
    bad_locks = set(brief.get("locked") or []) - set(LOCK_FIELDS)
    if bad_locks:
        errs.append(f"locked: неизвестные поля {sorted(bad_locks)} (допустимо: {', '.join(LOCK_FIELDS)})")
    assets = approved_assets(settings)
    ids = set()
    for i, sc in enumerate(brief.get("scenes") or []):
        sid = sc.get("id") or f"#{i + 1}"
        if sid in ids:
            errs.append(f"{sid}: повтор id сцены")
        ids.add(sid)
        kind = sc.get("type", "motion")
        if kind not in ("talking", "motion", "local"):
            errs.append(f"{sid}: type {kind!r} (talking | motion | local)")
        if kind != "local":
            a = sc.get("asset")
            if not a:
                errs.append(f"{sid}: нет asset")
            elif a not in assets:
                errs.append(f"{sid}: asset {a!r} не в approved_assets — утвердите кадр (studio character approve {a})")
            elif assets[a]["kind"] != "character":
                errs.append(f"{sid}: asset {a!r} — это {assets[a]['kind']}, а нужен кадр кота")
            if not sc.get("motion"):
                errs.append(f"{sid}: нет motion (действие кота на английском)")
            if sc.get("generator") not in ("kling", "higgsfield"):
                errs.append(f"{sid}: generator {sc.get('generator')!r} (kling | higgsfield)")
        if not str(sc.get("voiceover", "")).strip():
            errs.append(f"{sid}: нет voiceover")
        for ov in sc.get("overlays") or []:
            for img in ([ov.get("image")] if isinstance(ov.get("image"), str) else (ov.get("image") or [])) + \
                       [it.get("image") for it in ov.get("items") or []]:
                if img and img not in assets:
                    errs.append(f"{sid}: логотип {img!r} не найден в approved_assets (assets/logos)")
    if not brief.get("scenes"):
        errs.append("нет сцен")
    return errs


def brief_to_script(brief: dict) -> Script:
    """Перевод брифа в сценарий без творческих добавок: только обязательный хвост промпта."""
    scenes = []
    for sc in brief["scenes"]:
        kind = sc.get("type", "motion")
        local: dict = {"location": sc.get("location", "")}
        for k in ("overlays", "transition", "min_duration", "ambient"):
            if sc.get(k) is not None:
                local[k] = sc[k]
        if sc.get("model"):
            local["model"] = sc["model"]
        if kind == "talking":
            local["mode"] = sc.get("mode", "pro")
        if kind == "local":
            local.update(sc.get("local") or {})
            scenes.append(Scene(id=sc["id"], type=sc.get("local_type", "infographic"), duration=float(sc.get("duration", 4)),
                                generator="local", voiceover=sc["voiceover"], subtitle=sc.get("subtitle"),
                                visual=f"[{sc.get('location', '')}] {sc.get('visual', '')}".strip(), local=local))
            continue
        motion = str(sc["motion"]).strip().rstrip(".")
        if REQUIRED_SUFFIX not in motion:
            motion = f"{motion}, {REQUIRED_SUFFIX}"
        gen = sc["generator"]
        scenes.append(Scene(
            id=sc["id"], type="talking" if kind == "talking" else "character_motion",
            duration=float(sc.get("duration", 4)), generator=gen, reference=sc["asset"],
            voiceover=sc["voiceover"], subtitle=sc.get("subtitle"),
            visual=f"[{sc.get('location', '')}] {sc.get('visual') or sc['motion']}",
            prompts={gen: motion, "kling": motion}, local=local))
    goal = brief.get("goal") or {}
    return Script(id="", title=brief.get("title") or brief["project"], content_type=brief.get("content_type", "expert"),
                  topic=brief.get("topic", ""), audience=goal.get("audience", ""), cta=goal.get("cta", ""),
                  voice_preset=(brief.get("voice") or {}).get("preset", "default"),
                  target_duration=float(brief.get("target_duration", 50)), scenes=scenes)


# ---------------------------------------------------------------- locked-поля

def _sig(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def lock_signature(script: Script) -> dict:
    """Отпечатки защищённых полей по сценам: что сравнивать при каждом утверждении."""
    out = {"cta": _sig([script.cta, script.scenes[-1].voiceover if script.scenes else ""])}
    for s in script.scenes:
        ovs = s.local.get("overlays") or []
        out[s.id] = {
            "character": _sig([s.reference, s.type]),
            "location": _sig(s.local.get("location", "")),
            "text": _sig([s.voiceover, s.subtitle, [o.get("text") for o in ovs],
                          [[i.get("label") for i in o.get("items") or []] for o in ovs]]),
            "composition": _sig([s.reference, [{k: o.get(k) for k in ("style", "x", "y", "size", "image")} for o in ovs],
                                 s.local.get("crop")]),
        }
    return out


def write_lock(project: Project, brief: dict, script: Script) -> None:
    d = project.path / "director"
    d.mkdir(parents=True, exist_ok=True)
    (d / "brief.yaml").write_text(yaml.safe_dump(brief, allow_unicode=True, sort_keys=False), encoding="utf-8")
    lock = {"project": brief.get("project"), "version": brief.get("version", 1),
            "locked": list(brief.get("locked") or []),
            "scene_locks": {s.get("id"): list(s.get("locked") or []) for s in brief.get("scenes") or []},
            "signature": lock_signature(script),
            "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (d / "lock.json").write_text(json.dumps(lock, ensure_ascii=False, indent=1), encoding="utf-8")


def check_locks(project: Project, script: Script | None = None) -> list[str]:
    """Нарушения locked-полей: изменения против утверждённого брифа. Пустой список — всё в порядке."""
    p = project.path / "director" / "lock.json"
    if not p.exists():
        return []
    lock = json.loads(p.read_text(encoding="utf-8"))
    script = script or project.load_script()
    cur = lock_signature(script)
    ref = lock["signature"]
    errs = []
    glob = set(lock.get("locked") or [])
    if "cta" in glob and cur.get("cta") != ref.get("cta"):
        errs.append("CTA изменён (locked: cta)")
    ref_ids = [k for k in ref if k != "cta"]
    cur_ids = [k for k in cur if k != "cta"]
    if ref_ids != cur_ids and glob & {"text", "composition"}:
        errs.append(f"состав/порядок сцен изменён: было {ref_ids}, стало {cur_ids}")
    for sid in ref_ids:
        if sid not in cur:
            continue
        fields = glob | set(lock.get("scene_locks", {}).get(sid) or [])
        for f in ("character", "location", "text", "composition"):
            if f in fields and cur[sid][f] != ref[sid][f]:
                errs.append(f"{sid}: изменено поле «{f}» (locked)")
    return errs


def init_director_notes(project: Project, brief: dict) -> Path:
    p = project.path / "director" / "director_notes.md"
    if p.exists():
        return p
    rules = brief.get("review_rules") or {}
    goal = brief.get("goal") or {}
    lines = [f"# Заметки директора — {brief.get('project')} (бриф v{brief.get('version', 1)})", "",
             "Общие для всего эпизода решения и правки контент-директора. Claude читает перед каждой генерацией и монтажом.",
             "Новые записи — сверху, с датой. Правка конкретной сцены — в scenes/<id>/review.md.", "",
             "## Цель", f"- Аудитория: {goal.get('audience', '')}", f"- Задача: {goal.get('objective', '')}",
             f"- CTA: {goal.get('cta', '')}", "", "## Неизменяемое (locked)",
             *[f"- {x}" for x in brief.get("locked") or []], "", "## Правила ревью",
             *[f"- {k}: {v}" for k, v in rules.items()], "", "## Решения и правки", "- (пока нет)", ""]
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def import_brief(path: Path, settings: Settings, *, episode_id: str | None = None, dry_run: bool = False) -> tuple[Project | None, Script, list[str]]:
    brief = normalize_brief(load_brief(path), settings)
    errs = validate_brief(brief, settings)
    script = brief_to_script(brief) if not errs else Script(id="", title=str(brief.get("project")))
    if errs or dry_run:
        return None, script, errs
    eid = episode_id or f"episode-{str(brief['project']).replace('_', '-')}"
    try:
        project = create_project(script, settings, episode_id=eid)
    except ProjectError as e:
        raise BriefError(str(e)) from e
    write_lock(project, brief, script)
    init_director_notes(project, brief)
    from .state import init_states
    init_states(project, [s.id for s in script.scenes], approved=True)
    return project, script, []
