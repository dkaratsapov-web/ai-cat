"""CLI AI Content Studio: `studio <команда>` (или `python -m studio`)."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

import yaml

from .character.library import CharacterError, CharacterLibrary
from .config import get_settings, redact
from .costs.budget import Budget, BudgetError
from .db import DB
from .models import CONTENT_TYPES, Scene, Script, ScriptError
from .project import ProjectError, create_project, list_projects, open_project

TEMPLATES_DIR = "assets/templates/scripts"


def _db(settings) -> DB:
    return DB(settings.db_path)


def _split(v: str | None) -> list[str] | None:
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


# ------------------------------------------------------------------ commands

def cmd_doctor(a, s):
    from .diagnostics import run_doctor
    return 0 if run_doctor(s, check_api=a.check_api) else 1


def cmd_character(a, s):
    lib = CharacterLibrary(s)
    if a.action == "list":
        for r in lib.references():
            print(f"{r['id']:<22} {r['status']:<9} {r.get('use', ''):<8} {r.get('description', '')}")
        print("\nУтвердите референс после просмотра: studio character approve <id>")
    elif a.action == "add":
        if not a.ids or not a.path:
            raise CharacterError("Использование: studio character add <id> --path <файл>")
        r = lib.add(Path(a.path), a.ids[0], kind=a.kind, angle=a.angle or "", description=a.description or "",
                    prompt=a.prompt, model=a.model)
        print(f"Добавлен {r['id']} (status: pending). Проверьте изображение и утвердите: studio character approve {r['id']}")
    elif a.action in ("approve", "reject"):
        ids = list(a.ids or [])
        if a.all_pending:
            ids += [r["id"] for r in lib.references("pending") if r["id"] not in ids]
        if not ids:
            if a.all_pending:
                print("Нет кадров, ожидающих утверждения, — все уже обработаны (studio character list).")
                return 0
            raise CharacterError("Укажите id референсов (через пробел) или --all-pending")
        for rid in ids:
            r = lib.set_status(rid, "approved" if a.action == "approve" else "rejected", a.note or "")
            print(f"{r['id']}: {r['status']}")
    elif a.action == "prompt":
        print(lib.build_prompt(a.text or "<описание сцены>"))
        print("\nNegative:", lib.negative())
    return 0


def cmd_templates(a, s):
    d = s.root / TEMPLATES_DIR
    for p in sorted(d.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        print(f"{p.stem:<28} {CONTENT_TYPES.get(data.get('content_type'), ''):<16} {data.get('title')}")
    return 0


def cmd_new(a, s):
    if a.template:
        src = s.root / TEMPLATES_DIR / f"{a.template}.yaml"
        if not src.exists():
            raise ProjectError(f"Шаблон {a.template} не найден (studio templates)")
        script = Script.load(src)
        if a.title:
            script.title = a.title
    else:
        if not a.title:
            raise ProjectError("Укажите --title или --template")
        script = Script(
            id="", title=a.title, content_type=a.type, topic=a.brief or a.title, target_duration=a.duration,
            scenes=[
                Scene(id="s01", type="talking", duration=3, generator="kling", reference=None,
                      voiceover="<хук: первые 2–3 секунды>", visual="кот за ноутбуком смотрит в камеру",
                      prompts={"kling": "the cat looks into the camera and talks, subtle head movements"}),
                Scene(id="s02", type="infographic", duration=8, generator="local",
                      voiceover="<основная мысль>", local={"kind": "card", "title": "<заголовок>", "bullets": ["<пункт>"]}),
                Scene(id="s03", type="talking", duration=4, generator="kling",
                      voiceover="<призыв к действию>", prompts={"kling": "the cat nods confidently, talks to camera"}),
            ],
        )
    proj = create_project(script, s, episode_id=a.id)
    _db(s).log("create", {"title": script.title, "template": a.template}, episode=proj.id)
    print(f"Создан проект {proj.id}\nСценарий: {proj.script_path}")
    if a.brief:
        (proj.path / "script" / "brief.md").write_text(a.brief + "\n", encoding="utf-8")
    print(f"Дальше: studio script show {proj.id}")
    return 0


def cmd_list(a, s):
    for p in list_projects(s):
        m = p.meta()
        print(f"{p.id:<52} {m.get('status', '?'):<15} {m.get('title', '')}")
    return 0


def cmd_script(a, s):
    proj = open_project(a.episode, s)
    if a.action == "show":
        from .generation.runner import estimate, plan
        from .storyboard.render import storyboard_markdown
        script = proj.load_script()
        warnings = script.validate(s.get("video.min_duration"), s.get("video.max_duration"))
        est_table = None
        try:
            jobs = plan(proj, script, need_audio=False)
            est_table = estimate(proj, jobs, _db(s)).table() if jobs else "Платных генераций нет"
        except Exception as e:  # noqa: BLE001 — смета не должна мешать просмотру раскадровки
            est_table = f"Смета недоступна: {redact(str(e))}"
        md = storyboard_markdown(script, warnings, est_table)
        out = proj.path / "script" / "storyboard.md"
        out.write_text(md, encoding="utf-8")
        print(md)
        print(f"\nСтатус: {proj.status}. Раскадровка сохранена: {out}")
    elif a.action == "validate":
        warnings = proj.load_script().validate(s.get("video.min_duration"), s.get("video.max_duration"))
        print("Сценарий корректен." + ("".join(f"\n  ! {w}" for w in warnings)))
    elif a.action == "load":
        if not a.source:
            raise ProjectError("Укажите шаблон или файл: studio script load <эпизод> --from <шаблон|путь.yaml>")
        src = s.root / TEMPLATES_DIR / f"{a.source}.yaml"
        if not src.exists():
            src = Path(a.source)
        if not src.is_file():
            raise ProjectError(f"Не найден шаблон или файл: {a.source} (studio templates)")
        script = Script.load(src)
        script.id = proj.id
        script.validate(s.get("video.min_duration"), s.get("video.max_duration"))
        old = proj.script_path
        if old.exists():
            backup = old.with_name(f"script.prev-{int(time.time())}.yaml")
            backup.write_bytes(old.read_bytes())
            print(f"Прежний сценарий сохранён: {backup.name}")
        proj.save_script(script)
        _db(s).log("load_script", {"source": str(a.source)}, episode=proj.id)
        print(f"Сценарий {proj.id} заменён ({len(script.scenes)} сцен). Утверждение снято.\n"
              f"Дальше: studio script show {proj.id} — раскадровка и смета (готовые сцены переиспользуются)")
    elif a.action == "approve":
        script = proj.approve_script()
        _db(s).log("approve_script", {"fingerprint": script.fingerprint()}, episode=proj.id)
        print(f"Сценарий {proj.id} утверждён. Дальше: studio estimate {proj.id} → studio voice {proj.id}")
    return 0


def cmd_scene(a, s):
    proj = open_project(a.episode, s)
    if a.action == "import":
        src = Path(a.file)
        if not src.exists():
            raise ProjectError(f"Файл не найден: {src}")
        script = proj.load_script()
        script.scene(a.scene)
        dest = proj.dir("scenes") / f"{a.scene}.mp4"
        data = src.read_bytes()   # читаем до переименований: источником может быть файл из этой же папки
        # Убираем в архив и основной клип, и результат Lip Sync — иначе он перекрыл бы импортированный файл
        for old in (dest, dest.with_name(f"{a.scene}.lipsync.mp4")):
            if old.exists():
                old.rename(old.with_name(f"{old.stem}.prev-{int(old.stat().st_mtime)}.mp4"))
        dest.write_bytes(data)
        db = _db(s)
        db.create_job(episode=proj.id, scene_id=a.scene, provider="manual", kind="import", model=None,
                      params={"source": str(src)}, idempotency_key=f"import-{proj.id}-{a.scene}-{dest.stat().st_mtime}",
                      est_cost_usd=0, paid=False, status="imported")
        db.log("import_scene", {"scene": a.scene, "file": src.name}, episode=proj.id)
        print(f"{a.scene}: импортирован {src.name}. Пересоберите: studio assemble {proj.id}")
        return 0
    script = proj.load_script()
    sc = script.scene(a.scene)
    changed = []
    for field_name in ("voiceover", "visual", "generator", "type", "reference", "animation"):
        val = getattr(a, field_name, None)
        if val is not None:
            setattr(sc, field_name, val)
            changed.append(field_name)
    if a.duration is not None:
        sc.duration = a.duration
        changed.append("duration")
    if a.prompt is not None:
        sc.prompts[a.prompt_for or sc.generator] = a.prompt
        changed.append("prompt")
    if not changed:
        print(yaml.safe_dump(script.to_dict()["scenes"][script.scenes.index(sc)], allow_unicode=True, sort_keys=False))
        return 0
    script.validate(s.get("video.min_duration"), s.get("video.max_duration"))
    proj.save_script(script)
    if proj.status not in ("draft", "cancelled"):
        proj.set_status("draft")
    _db(s).log("edit_scene", {"scene": a.scene, "fields": changed}, episode=proj.id)
    print(f"{a.scene}: обновлено ({', '.join(changed)}). Сценарий снова требует утверждения: studio script approve {proj.id}")
    return 0


def cmd_cancel(a, s):
    proj = open_project(a.episode, s)
    proj.set_status("cancelled")
    _db(s).log("cancel", "", episode=proj.id)
    print(f"Производство {proj.id} отменено. Платные операции заблокированы до повторного утверждения сценария.")
    return 0


def cmd_estimate(a, s):
    from .generation.runner import estimate, plan
    from .generation.voice import estimate_voice
    proj = open_project(a.episode, s)
    script = proj.load_script()
    db = _db(s)
    pricing = s.load_yaml("config/pricing.yaml")
    tts_name = s.get("providers.tts", "elevenlabs")
    v = estimate_voice(proj, script, tts_name, pricing)
    print("Озвучка:\n" + v.table())
    jobs = plan(proj, script, need_audio=False)
    est = estimate(proj, jobs, db)
    print("\nВидео:\n" + (est.table() if jobs else "Платных генераций нет"))
    note = " (озвучка входит в подписку)" if pricing.get(tts_name, {}).get("subscription_based") else ""
    print(f"\nИтого видео: ${est.total:.2f}; озвучка: ${v.total:.2f}{note}")
    print("Говорящие сцены оцениваются по плановой длительности; точная сумма — после озвучки.")
    st = Budget(s, db).status(proj.id)
    print(f"Бюджет месяца: потрачено ${st['spent_month_usd']:.2f} из ${st['monthly_budget_usd']:.2f}; "
          f"на ролик: ${st['spent_episode_usd']:.2f} из ${st['per_video_limit_usd']:.2f}")
    return 0


def cmd_voice(a, s):
    from .generation.voice import generate_voice
    proj = open_project(a.episode, s)
    res = generate_voice(proj, _db(s), provider_name=a.provider, scenes=_split(a.scenes), assume_yes=a.yes, force=a.force)
    total = sum(m["duration"] for m in res.values())
    for sid, m in res.items():
        print(f"  {sid}: {m['duration']:.2f}с ({m['provider']})")
    print(f"Озвучка: {total:.2f}с. Дальше: studio generate {proj.id}")
    return 0


DEFAULT_SAMPLE_VOICES = ("alena", "jane", "dasha", "julia", "lera", "masha", "marina", "omazh",
                         "ermil", "filipp", "zahar", "alexander", "kirill", "anton", "madirus")
DEFAULT_SAMPLE_TEXT = "Директ сливает бюджет? Мяу. Сейчас разберёмся за тридцать секунд."


def cmd_voice_samples(a, s):
    """Одна фраза разными голосами — чтобы выбрать голос кота на слух."""
    from .generation.voice import load_preset
    from .integrations import tts_provider
    from .integrations.base import ProviderError
    from .integrations.tts import apply_voice_effect
    prov = tts_provider(a.provider, s)
    ok, why = prov.configured()
    if not ok:
        raise RuntimeError(f"TTS '{a.provider}' не настроен: {why}")
    voices = _split(a.voices) or list(DEFAULT_SAMPLE_VOICES)
    base = dict(load_preset(s, "default"))
    pricing = s.load_yaml("config/pricing.yaml")
    per = prov.estimate_usd(a.text, base, pricing)
    rub = per * float(pricing.get("yandex", {}).get("rub_per_usd", 90))
    print(f"{len(voices)} образцов × ~{rub:.2f} ₽ ≈ {rub * len(voices):.1f} ₽")
    if not Budget(s, _db(s)).confirm("Озвучить образцы?", a.yes):
        return 0
    out_dir = s.data_dir / "voice_samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    db = _db(s)
    made = []
    for v in voices:
        preset = {**base, "voice": v, "role": a.role, "speed": a.speed, "pitch_shift": a.pitch}
        tag = "_".join(x for x in (v, a.role or "", f"p{int(a.pitch)}" if a.pitch else "", f"s{a.speed}") if x)
        dest = out_dir / f"{tag}.wav"
        try:
            prov.synthesize(a.text, preset, dest)
        except ProviderError as e:
            print(f"  {v}: не получилось ({redact(str(e))[:120]})")
            continue
        db.create_job(episode="voice-samples", scene_id=v, provider=a.provider, kind="tts", model=v,
                      params={"chars": len(a.text)}, idempotency_key=f"sample-{tag}-{a.text}",
                      est_cost_usd=per, status="succeeded")
        made.append(dest)
        names = [dest.name]
        for k in _split(a.variants) or []:
            factor = float(k)
            if abs(factor - 1.0) < 1e-3:
                continue
            var = out_dir / f"{tag}_cat{factor:g}.wav"
            shutil.copy2(dest, var)
            apply_voice_effect(var, factor, a.formant)
            names.append(var.name)
        print(f"  {v}: {', '.join(names)}")
    print(f"\nОбразцы: {out_dir}\nФайлы *_catX — тот же голос, обработанный локально (X — во сколько раз выше тон, "
          "бесплатно).\nНапишите, какой файл понравился, — впишем голос и эффект в config/voices.yaml.")
    if sys.platform == "win32" and made:
        os.startfile(out_dir)  # type: ignore[attr-defined]  # открыть папку в Проводнике
    return 0


def cmd_generate(a, s):
    from .generation.runner import Runner
    proj = open_project(a.episode, s)
    db = _db(s)
    scenes = _split(a.scenes)
    if a.review:   # режим директора: одна сцена → стоп → ревью
        from .director import qc, state
        state.sync_from_jobs(proj, db)
        st = state.load(proj)
        script = proj.load_script()
        queue = [sc.id for sc in script.scenes if sc.generator != "local"
                 and (st.get(sc.id) or {}).get("status", "approved") in ("approved", "revise")]
        scenes = scenes[:1] if scenes else queue[:1]
        if not scenes:
            print("Нет сцен в статусе approved/revise — генерировать нечего.")
            return 0
        print(f"Режим ревью: генерирую только {scenes[0]}, затем стоп.")
    Runner(proj, db).generate(scenes, assume_yes=a.yes, regenerate=_split(a.regenerate), wait=not a.no_wait)
    from .director.review import write_cost_csv
    write_cost_csv(db, s.root / "logs" / "costs.csv")   # общий журнал расходов (все эпизоды)
    if a.review:
        from .director import qc, state
        state.sync_from_jobs(proj, db)
        sid = scenes[0]
        if (state.load(proj).get(sid) or {}).get("status") == "generated":
            state.set_status(proj, sid, "review", "ждёт ревью директора")
            info = qc.prepare_identity_qc(proj, sid)
            print(f"\n{sid}: готово, статус review. СТОП.\n  Видео: scenes/{sid}.mp4 (версии: scenes/{sid}/)\n"
                  f"  QC идентичности: {info['sheet']} (окрас: {info['similarity']} — {info['hint']})\n"
                  f"  Ревью директора: studio director review-scene {proj.id} {sid}  (без OpenAI: --manual)")
    return 0


def cmd_status(a, s):
    from .generation.runner import Runner
    proj = open_project(a.episode, s)
    db = _db(s)
    if a.refresh:
        Runner(proj, db).refresh()
    print(f"Проект {proj.id}: {proj.status}")
    for j in db.jobs_for(proj.id):
        cost = j["actual_cost_usd"] if j["actual_cost_usd"] is not None else j["est_cost_usd"]
        print(f"  {j['id'][:8]} {j['scene_id'] or '-':<5} {j['provider']:<7} {j['kind']:<11} {j['status']:<11} "
              f"${cost or 0:.3f} {j['result_path'] or ''} {('— ' + redact(j['error'])[:80]) if j['error'] else ''}")
    script = proj.load_script()
    for sc in script.scenes:
        src = proj.scene_source(sc.id)
        print(f"  сцена {sc.id}: {sc.generator:<7} {'исходник есть' if src or sc.generator == 'local' else 'нет исходника'}")
    return 0


def cmd_jobs(a, s):
    from .generation.runner import resolve_job
    j = resolve_job(_db(s), a.job, a.status, a.note or "")
    print(f"{j['id'][:8]}: {j['status']}")
    return 0


def cmd_assemble(a, s):
    from .editing.assemble import assemble
    proj = open_project(a.episode, s)
    if a.final:
        from .director import state
        state.sync_from_jobs(proj, _db(s))
        blocking = state.all_final(proj)
        if blocking:
            raise ProjectError(f"Финальный монтаж невозможен: не в final — {', '.join(blocking)}")
    suffix = ("-nomusic" if a.no_music else "") + ("-nosubs" if a.no_subs else "")
    out = assemble(proj, music=Path(a.music) if a.music else None, burn_subtitles=not a.no_subs,
                   use_music=not a.no_music, suffix=suffix)
    _db(s).log("assemble", {"file": out.name}, episode=proj.id)
    print(f"Готово: {out}\nДальше: studio qa {proj.id}")
    return 0


def cmd_qa(a, s):
    from .quality.checks import run_qa, save_report
    proj = open_project(a.episode, s)
    rep = run_qa(proj)
    save_report(proj, rep)
    print(rep.text())
    proj.set_status("qa_passed" if rep.passed else "qa_failed")
    _db(s).log("qa", {"passed": rep.passed}, episode=proj.id)
    if rep.passed:
        print(f"\nПосмотрите ролик и утвердите: studio approve {proj.id}  (или исправьте сцену и пересоберите)")
    else:
        bad = sorted({c.scene for c in rep.checks if c.status == "fail" and c.scene})
        if bad:
            print(f"\nПроблемные сцены: {', '.join(bad)}. Переделать: studio generate {proj.id} --regenerate {','.join(bad)}")
    return 0 if rep.passed else 2


def cmd_approve(a, s):
    proj = open_project(a.episode, s)
    if proj.status not in ("qa_passed",):
        raise ProjectError(f"Сначала техническая проверка должна пройти успешно (сейчас: {proj.status})")
    proj.set_status("final_approved", final_note=a.note or "")
    _db(s).log("final_approve", a.note or "", episode=proj.id)
    print(f"Ролик утверждён. Дальше: studio package {proj.id}")
    return 0


def cmd_package(a, s):
    from .publishing.package import package
    proj = open_project(a.episode, s)
    if proj.status != "final_approved" and not a.force:
        raise ProjectError("Пакет публикации готовится после утверждения ролика (studio approve) или с --force")
    pdir = package(proj)
    print(f"Пакет публикации: {pdir}\nПубликация выполняется вручную после вашего подтверждения.")
    return 0


def cmd_pipeline(a, s):
    """Полный цикл. С --mock — бесплатный тест на копии проекта (заглушки вместо платных API)."""
    from .editing.assemble import assemble
    from .generation.runner import Runner
    from .generation.voice import generate_voice
    from .quality.checks import run_qa, save_report
    proj = open_project(a.episode, s)
    db = _db(s)
    if a.mock:
        test_id = f"{proj.id}--mock"
        tp = s.projects_dir / test_id
        if tp.exists():
            shutil.rmtree(tp)
        src_proj = proj
        proj = create_project(src_proj.load_script(), s, episode_id=test_id)
        if (src_proj.path / "imports").exists():
            shutil.copytree(src_proj.path / "imports", proj.path / "imports", dirs_exist_ok=True)
        proj.approve_script()
        s.data["override_generator"] = "mock"
        print(f"Тестовый прогон на копии {test_id}: озвучка и видео — заглушки, оплаты нет.")
    print("1/4 Озвучка")
    generate_voice(proj, db, provider_name="mock" if a.mock else None, assume_yes=a.yes)
    print("2/4 Генерация сцен")
    # Смета говорящих сцен зависит от длины озвучки — её нужно увидеть и подтвердить отдельно
    Runner(proj, db).generate(assume_yes=a.yes and a.mock)
    print("3/4 Монтаж")
    out = assemble(proj)
    print(f"   {out}")
    print("4/4 Техническая проверка")
    rep = run_qa(proj)
    save_report(proj, rep)
    proj.set_status("qa_passed" if rep.passed else "qa_failed")
    print(rep.text())
    return 0 if rep.passed else 2


def cmd_web(a, s):
    from .web.server import serve
    serve(s, port=a.port, open_browser=not a.no_browser)
    return 0


def cmd_costs(a, s):
    db = _db(s)
    if a.csv:
        from .director.review import write_cost_csv
        out = write_cost_csv(db, Path(a.csv), a.episode)
        print(f"Журнал расходов: {out}")
        return 0
    st = Budget(s, db).status(a.episode)
    for k, v in st.items():
        print(f"{k:<22} {v}")
    print("\nПоследние записи расходов:")
    for r in db.cost_rows(a.episode, limit=a.limit):
        print(f"  {r['ts']} {r['episode'] or '-':<40} {r['provider']:<11} {r['kind']:<9} ${r['amount_usd']:.3f} {r['note'] or ''}")
    print("\nОценочные суммы могут отличаться от фактического списания. Сверяйте с кабинетом провайдера.")
    return 0


def cmd_brief(a, s):
    from .director import brief as br
    if a.action == "check":
        proj = open_project(a.target, s)
        v = br.check_locks(proj)
        print("Locked-поля в порядке." if not v else "Нарушения locked:\n  - " + "\n  - ".join(v))
        return 0 if not v else 1
    proj, script, errs = br.import_brief(Path(a.target), s, episode_id=a.id, dry_run=a.dry_run)
    if errs:
        print("Бриф НЕ принят:\n  - " + "\n  - ".join(errs))
        return 1
    if a.dry_run:
        print(f"Бриф корректен (dry-run, ничего не создано): {len(script.scenes)} сцен, ~{script.planned_duration:.0f} с")
        for sc in script.scenes:
            print(f"  {sc.id}  {sc.type:<16} {sc.generator:<10} {sc.reference or '-':<18} {sc.subtitle_text[:60]}")
        return 0
    print(f"Создан проект {proj.id} из брифа. Статусы сцен: approved. Locked-поля зафиксированы.\n"
          f"  Заметки директора: {proj.path / 'director' / 'director_notes.md'}\n  Дальше: studio script show {proj.id}")
    return 0


def cmd_scene_status(a, s):
    from .director import state
    proj = open_project(a.episode, s)
    state.sync_from_jobs(proj, _db(s))
    if a.scene and a.set:
        state.set_status(proj, a.scene, a.set, a.note or "")
    for sid, st in state.load(proj).items():
        qc = (st.get("identity_qc") or {}).get("verdict", "—")
        last = st["history"][-1]["at"][:16] if st.get("history") else ""
        print(f"  {sid:<5} {st['status']:<10} v{st.get('versions', 0)}  QC кота: {qc:<5} {last}")
    return 0


def cmd_idqc(a, s):
    from .director import qc
    proj = open_project(a.episode, s)
    if a.verdict:
        r = qc.record_verdict(proj, a.scene, a.verdict, a.notes or "")
        print(f"{a.scene}: QC идентичности = {r['verdict']} (версия v{r['version']})")
        return 0
    info = qc.prepare_identity_qc(proj, a.scene)
    print(f"{a.scene}: контактный лист {info['sheet']}\n  окрас: сходство {info['similarity']} — {info['hint']} (подсказка, не вердикт)\n"
          f"  Вердикт ставит человек: studio idqc {proj.id} {a.scene} --pass | --fail --notes \"...\"")
    return 0


def cmd_review(a, s):
    from datetime import datetime
    from .director import state
    proj = open_project(a.episode, s)
    if a.action == "import":   # ответ ChatGPT вручную (OpenAI недоступен)
        from .director.bridge import import_review
        if not a.file:
            raise ProjectError("Нужен --file с ответом директора (JSON)")
        data = import_review(proj, _db(s), a.scene, Path(a.file))
        print(f"{a.scene}: ревью импортировано, статус директора: {data.get('status')}")
        return 0
    if a.action == "apply":    # применить последнее ревью директора по сцене
        st = state.load(proj).get(a.scene) or {}
        d = st.get("director")
        if not d:
            raise ProjectError(f"{a.scene}: нет ревью директора (studio director review-scene {proj.id} {a.scene})")
        qc_v = (st.get("identity_qc") or {}).get("verdict")
        if d["status"] == "approved" and a.approve:
            state.set_status(proj, a.scene, "final", "директор approved + подтверждение пользователя", override=a.override)
            print(f"{a.scene}: final.")
        elif d["status"] == "approved":
            print(f"{a.scene}: директор approved, QC кота: {qc_v}. В final — только с вашим подтверждением: "
                  f"studio review apply {proj.id} {a.scene} --approve")
        else:
            state.set_status(proj, a.scene, "revise", "ревью директора: revise", force=True)
            print(f"{a.scene}: revise. Предложение директора (НЕ применено, платно):\n  {d.get('revision_prompt')}\n"
                  f"  Проблемы: {'; '.join(d.get('issues') or [])}\n"
                  f"  Если согласны — я обновлю промпт, покажу смету, перегенерация только после вашего «да».")
        return 0
    text = Path(a.file).read_text(encoding="utf-8") if a.file else (a.text or "")
    if not text.strip():
        raise ProjectError("Пустое ревью: --text \"...\" или --file review.md")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if a.scene == "all":
        notes = proj.path / "director" / "director_notes.md"
        old = notes.read_text(encoding="utf-8") if notes.exists() else "## Решения и правки\n"
        mark = "## Решения и правки"
        new = old.replace(mark, f"{mark}\n- {stamp}: {text.strip()}", 1) if mark in old else old + f"\n- {stamp}: {text.strip()}\n"
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text(new, encoding="utf-8")
        print(f"Общая правка записана в {notes}")
        return 0
    rdir = proj.dir("scenes") / a.scene
    rdir.mkdir(parents=True, exist_ok=True)
    with open(rdir / "review.md", "a", encoding="utf-8") as f:
        f.write(f"\n## {stamp} — ревью директора\n{text.strip()}\n")
    target = "final" if a.approve else "revise"
    state.sync_from_jobs(proj, _db(s))
    state.set_status(proj, a.scene, target, text.strip()[:200])
    print(f"{a.scene}: ревью записано, статус {target}." + ("" if a.approve else
          " Внесите правки (промпт/кадр — через утверждение) и: studio generate " + proj.id + f" --review --scenes {a.scene} --regenerate {a.scene}"))
    return 0


def cmd_review_pack(a, s):
    from .director.review import build_review_pack
    proj = open_project(a.episode, s)
    z = build_review_pack(proj, _db(s), video=Path(a.video) if a.video else None)
    print(f"Review-пакет: {z}\n  Внутри: review.md, preview.mp4, contact_sheet.jpg, frames/, costs.csv, director_notes.md")
    return 0


def cmd_director(a, s):
    from .director import bridge as br
    from .director import state
    if a.action == "ping":
        return _director_ping(a, s)
    if not a.episode:
        raise ProjectError(f"Укажите эпизод: studio director {a.action} <эпизод>")
    proj = open_project(a.episode, s)
    db = _db(s)
    mode = "mock" if a.mock else ("manual" if a.manual else "openai")
    if a.action in ("sync", "status"):
        st = state.sync_from_jobs(proj, db)
        for sid, x in st.items():
            d = (x.get("director") or {}).get("status", "—")
            q = (x.get("identity_qc") or {}).get("verdict", "—")
            sr = (x.get("script_review") or {}).get("status", "—")
            print(f"  {sid:<5} {x['status']:<13} v{x.get('versions', 0)}  сценарий: {sr:<8} директор: {d:<8} QC кота: {q}")
        return 0
    if a.action == "export-review-package":
        from datetime import datetime as _dt
        pkg = s.root / "director_bridge" / "review_packages" / proj.id / (_dt.now().strftime("%Y%m%d-%H%M%S") + "_export")
        br.export_final_package(proj, db, pkg, Path(a.video) if a.video else proj.final_video)
        print(f"Review-пакет: {pkg}")
        return 0
    if a.action == "chat":
        from .director import chat
        out = chat.render(proj, db)
        print(f"Чат эпизода: {out}")
        if a.open:
            import webbrowser
            webbrowser.open(out.resolve().as_uri())
        return 0
    if a.action == "ask" and not a.scene:
        raise ProjectError(f'Напишите вопрос: studio director ask {proj.id} "текст вопроса"')
    if mode == "manual" and a.action == "ask":
        raise ProjectError("Вопрос без OpenAI не отправить — задайте его в ChatGPT вручную")
    if mode == "openai" and not a.yes:
        ok = input("Запрос к OpenAI платный (обычно центы; цена — в кабинете OpenAI). Отправить? [да/нет]: ")
        if ok.strip().lower() not in ("да", "y", "yes", "д"):
            print("Отменено.")
            return 0
    b = br.Bridge(proj, db, mode=mode)
    if a.action == "ask":
        ans = b.ask(a.scene)
        if ans:
            print(f"Директор:\n{ans}")
        from .director import chat
        print(f"Чат эпизода: {chat.render(proj, db)}")
        return 0
    if a.action == "review":
        data = b.script_review()
    elif a.action == "review-scene":
        if not a.scene:
            raise ProjectError("Укажите сцену: studio director review-scene <эпизод> sNN")
        data = b.scene_review(a.scene)
    else:
        data = b.final_review(Path(a.video) if a.video else None)
    from .director import chat
    chat.render(proj, db)
    if data:
        print(f"Директор: {data.get('status')}" + (f", оценка {data['final_score']}" if "final_score" in data else ""))
        print(f"  Ответ: {proj.path / 'director' / 'latest_review.md'}")
    return 0


def _director_ping(a, s):
    """Проверка связи с OpenAI: 1) бесплатно — ключ и доступ к модели; 2) после «да» — один крошечный запрос."""
    from .director.bridge import load_config
    from .director.openai_client import DirectorUnavailable, OpenAIDirector
    cfg = load_config(s)
    od = OpenAIDirector(cfg)
    try:
        models = od.list_models()
    except DirectorUnavailable as e:
        print(f"✗ {e}\n  Без OpenAI работает ручной режим: --manual")
        return 1
    print(f"✓ Ключ принят, проекту доступно моделей: {len(models)}")
    gpt = [m for m in models if m.startswith(("gpt-5", "gpt-4.1", "gpt-4o", "o3", "o4"))]
    print("  Подходящие: " + (", ".join(gpt[:15]) or "—"))
    if cfg["model"] not in models:
        print(f"✗ Модель из director_bridge/config/bridge.yaml ({cfg['model']}) проекту недоступна — "
              f"впишите одну из подходящих в поле model")
        return 1
    print(f"✓ Модель {cfg['model']} доступна")
    if not a.yes:
        ok = input("Отправить один тестовый запрос в Responses API (десятки токенов, доли цента)? [да/нет]: ")
        if ok.strip().lower() not in ("да", "y", "yes", "д"):
            print("Бесплатная часть проверки пройдена. Тестовый запрос не отправлен.")
            return 0
    try:
        r = od.ping()
    except DirectorUnavailable as e:
        print(f"✗ {e}")
        return 1
    print(f"1. HTTP/API status: {r['http_status']}" + (f" / {r['api_status']}" if r.get("api_status") else ""))
    if r.get("error_code") or r["http_status"] >= 400:
        print(f"   Ошибка: {r.get('error_code')} — {r.get('error')}")
        print(f"4. request id: {r.get('request_id') or '—'}")
        return 1
    print(f"2. model used: {r.get('model')}")
    print(f"3. returned text: {r.get('text')!r}")
    print(f"4. request id: {r.get('request_id') or '—'} (response id: {r.get('response_id')})")
    u = r.get("usage") or {}
    print(f"5. usage: input {u.get('input_tokens')}, output {u.get('output_tokens')}, total {u.get('total_tokens')} токенов "
          "(стоимость API не возвращает — смотрите Usage в кабинете OpenAI)")
    return 0 if (r.get("text") or "").strip() == "OPENAI_OK" else 1


def cmd_chat(a, s):
    from .director.server import ChatServer
    proj = open_project(a.episode, s)
    ChatServer(proj, _db(s), mode="mock" if a.mock else "openai").serve(port=a.port, open_browser=not a.no_open)
    return 0


def cmd_assets(a, s):
    from .director.assets import approved_assets, export_index, pending_character_refs
    if a.export:
        print(f"Записано: {export_index(s)}")
    items = approved_assets(s)
    for k, v in sorted(items.items(), key=lambda kv: (kv[1]["kind"], kv[0])):
        print(f"  {v['kind']:<10} {k:<24} {v.get('use', ''):<8} {v.get('location', ''):<15} {v.get('description', '')[:50]}")
    pend = pending_character_refs(s)
    if pend:
        print(f"\nЕщё не утверждены (в бриф не попадут): {', '.join(pend)}")
    return 0


def cmd_history(a, s):
    db = _db(s)
    for e in reversed(db.events(a.episode, limit=a.limit)):
        print(f"{e['ts']} {e['episode'] or '-':<40} {e['action']:<15} {redact(e['detail'] or '')[:120]}")
    return 0


# ------------------------------------------------------------------ parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="studio", description="AI Content Studio — фабрика Reels с котом-маркетологом")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="диагностика окружения")
    d.add_argument("--check-api", action="store_true", help="бесплатная проверка подключения/баланса API")
    d.set_defaults(fn=cmd_doctor)

    c = sub.add_parser("character", help="библиотека персонажа")
    c.add_argument("action", choices=["list", "add", "approve", "reject", "prompt"])
    c.add_argument("ids", nargs="*", help="id референса (для approve/reject можно несколько через пробел)")
    c.add_argument("--all-pending", action="store_true", help="(approve/reject) все ожидающие утверждения")
    c.add_argument("--path")
    c.add_argument("--kind", default="reference", choices=["original", "portrait", "reference"])
    c.add_argument("--angle")
    c.add_argument("--description")
    c.add_argument("--prompt", help="исходный промпт генерации референса")
    c.add_argument("--model", help="модель, которой создан референс")
    c.add_argument("--note")
    c.add_argument("--text", help="(prompt) описание сцены")
    c.set_defaults(fn=cmd_character)

    sub.add_parser("templates", help="стартовые сценарии").set_defaults(fn=cmd_templates)

    n = sub.add_parser("new", help="создать проект ролика")
    n.add_argument("--title")
    n.add_argument("--template", help="имя стартового сценария (studio templates)")
    n.add_argument("--id", help="свой id проекта (латиница, цифры, -), например episode-fitness-top5")
    n.add_argument("--type", default="expert", choices=list(CONTENT_TYPES))
    n.add_argument("--duration", type=float, default=30)
    n.add_argument("--brief", help="задание обычным текстом")
    n.set_defaults(fn=cmd_new)

    sub.add_parser("list", help="проекты").set_defaults(fn=cmd_list)

    sc = sub.add_parser("script", help="сценарий: show | validate | approve | load")
    sc.add_argument("action", choices=["show", "validate", "approve", "load"])
    sc.add_argument("episode")
    sc.add_argument("--from", dest="source", help="(load) имя шаблона или путь к script.yaml")
    sc.set_defaults(fn=cmd_script)

    se = sub.add_parser("scene", help="изменить сцену или импортировать готовый клип")
    se.add_argument("action", choices=["set", "show", "import"])
    se.add_argument("episode")
    se.add_argument("scene")
    se.add_argument("file", nargs="?", help="(import) путь к видеофайлу")
    se.add_argument("--voiceover")
    se.add_argument("--visual")
    se.add_argument("--animation")
    se.add_argument("--duration", type=float)
    se.add_argument("--generator", choices=["kling", "hedra", "runway", "local", "manual", "mock"])
    se.add_argument("--type")
    se.add_argument("--reference")
    se.add_argument("--prompt")
    se.add_argument("--prompt-for", help="для какого генератора промпт (по умолчанию — текущий)")
    se.set_defaults(fn=cmd_scene)

    x = sub.add_parser("cancel", help="отменить производство")
    x.add_argument("episode")
    x.set_defaults(fn=cmd_cancel)

    e = sub.add_parser("estimate", help="смета до генерации")
    e.add_argument("episode")
    e.set_defaults(fn=cmd_estimate)

    v = sub.add_parser("voice", help="озвучка")
    v.add_argument("episode")
    v.add_argument("--provider", choices=["yandex", "elevenlabs", "manual", "mock"])
    v.add_argument("--scenes")
    v.add_argument("--force", action="store_true", help="перегенерировать даже без изменений текста")
    v.add_argument("--yes", action="store_true", help="подтвердить расход без вопроса")
    v.set_defaults(fn=cmd_voice)

    vs = sub.add_parser("voice-samples", help="одна фраза разными голосами — выбрать голос кота")
    vs.add_argument("--voices", help="через запятую; по умолчанию — 15 русских голосов SpeechKit")
    vs.add_argument("--text", default=DEFAULT_SAMPLE_TEXT)
    vs.add_argument("--role", default=None, help="амплуа, например good / friendly / neutral (не у всех голосов)")
    vs.add_argument("--pitch", type=float, default=0.0, help="сдвиг высоты в Гц, например 150 — выше и мягче")
    vs.add_argument("--speed", type=float, default=1.1)
    vs.add_argument("--variants", default="1.2,1.35,1.5",
                    help="локальные варианты тона для каждого голоса (бесплатно), например 1.2,1.35,1.5")
    vs.add_argument("--formant", default="shifted", choices=["shifted", "preserved"],
                    help="shifted — мультяшный тембр; preserved — тот же голос, но выше")
    vs.add_argument("--provider", default="yandex", choices=["yandex"])
    vs.add_argument("--yes", action="store_true")
    vs.set_defaults(fn=cmd_voice_samples)

    g = sub.add_parser("generate", help="генерация AI-сцен (платно, с подтверждением)")
    g.add_argument("episode")
    g.add_argument("--scenes", help="только эти сцены: s01,s03")
    g.add_argument("--regenerate", help="переделать сцены (новая платная попытка): s02")
    g.add_argument("--yes", action="store_true", help="подтвердить смету без вопроса")
    g.add_argument("--no-wait", action="store_true", help="не ждать завершения (потом: studio status --refresh)")
    g.add_argument("--review", action="store_true", help="режим директора: одна сцена → стоп → ревью")
    g.set_defaults(fn=cmd_generate)

    st = sub.add_parser("status", help="статус задач и сцен")
    st.add_argument("episode")
    st.add_argument("--refresh", action="store_true", help="опросить провайдеров и скачать готовое")
    st.set_defaults(fn=cmd_status)

    jb = sub.add_parser("jobs", help="ручное разрешение неясных задач")
    jb.add_argument("action", choices=["resolve"])
    jb.add_argument("job")
    jb.add_argument("--status", required=True, choices=["failed", "cancelled", "succeeded"])
    jb.add_argument("--note")
    jb.set_defaults(fn=cmd_jobs)

    a = sub.add_parser("assemble", help="монтаж ролика (бесплатно, локально)")
    a.add_argument("episode")
    a.add_argument("--music")
    a.add_argument("--no-subs", action="store_true")
    a.add_argument("--no-music", action="store_true", help="версия без музыки (файл …-nomusic.mp4)")
    a.add_argument("--final", action="store_true", help="только если все AI-сцены в статусе final (режим директора)")
    a.set_defaults(fn=cmd_assemble)

    q = sub.add_parser("qa", help="техническая проверка")
    q.add_argument("episode")
    q.set_defaults(fn=cmd_qa)

    ap = sub.add_parser("approve", help="утвердить готовый ролик после просмотра")
    ap.add_argument("episode")
    ap.add_argument("--note")
    ap.set_defaults(fn=cmd_approve)

    pk = sub.add_parser("package", help="подготовить публикацию")
    pk.add_argument("episode")
    pk.add_argument("--force", action="store_true")
    pk.set_defaults(fn=cmd_package)

    pl = sub.add_parser("pipeline", help="озвучка → генерация → монтаж → проверка")
    pl.add_argument("episode")
    pl.add_argument("--mock", action="store_true", help="бесплатный тестовый прогон на копии проекта")
    pl.add_argument("--yes", action="store_true")
    pl.set_defaults(fn=cmd_pipeline)

    wb = sub.add_parser("web", help="веб-панель управления в браузере (локально)")
    wb.add_argument("--port", type=int, default=8765)
    wb.add_argument("--no-browser", action="store_true", help="не открывать браузер автоматически")
    wb.set_defaults(fn=cmd_web)

    co = sub.add_parser("costs", help="расходы и бюджет")
    co.add_argument("episode", nargs="?")
    co.add_argument("--limit", type=int, default=30)
    co.add_argument("--csv", help="выгрузить журнал расходов в CSV: date,project,scene,provider,model,duration,cost,status")
    co.set_defaults(fn=cmd_costs)

    # --- режим контент-директора (ChatGPT утверждает, Claude исполняет)
    b = sub.add_parser("brief", help="Director Brief: import <file.yaml> [--dry-run] | check <эпизод>")
    b.add_argument("action", choices=["import", "check"])
    b.add_argument("target", help="файл брифа (import) или id эпизода (check)")
    b.add_argument("--id", help="id проекта (по умолчанию episode-<project>)")
    b.add_argument("--dry-run", action="store_true", help="только проверить и показать, ничего не создавать")
    b.set_defaults(fn=cmd_brief)

    ss = sub.add_parser("scene-status", help="статусы сцен draft→approved→generating→generated→review→revise→final")
    ss.add_argument("episode")
    ss.add_argument("scene", nargs="?")
    ss.add_argument("--set", help="новый статус сцены")
    ss.add_argument("--note")
    ss.set_defaults(fn=cmd_scene_status)

    iq = sub.add_parser("idqc", help="QC идентичности кота: материалы или вердикт --pass/--fail")
    iq.add_argument("episode")
    iq.add_argument("scene")
    iq.add_argument("--pass", dest="verdict", action="store_const", const="pass")
    iq.add_argument("--fail", dest="verdict", action="store_const", const="fail")
    iq.add_argument("--notes")
    iq.set_defaults(fn=cmd_idqc)

    rv = sub.add_parser("review", help="ревью: add (своё/директора текстом) | apply (ответ директора) | import (ответ ChatGPT вручную)")
    rv.add_argument("action", choices=["add", "apply", "import"])
    rv.add_argument("episode")
    rv.add_argument("scene", help="sNN | all (add: в director_notes) | script | final (import)")
    rv.add_argument("--override", action="store_true", help="ручное решение пользователя: final вопреки вердикту")
    rv.add_argument("--text")
    rv.add_argument("--file")
    rv.add_argument("--approve", action="store_true", help="сцена утверждена директором → final (нужен QC кота pass)")
    rv.set_defaults(fn=cmd_review)

    rp = sub.add_parser("review-pack", help="пакет для ревью директора (zip)")
    rp.add_argument("episode")
    rp.add_argument("--video", help="какой ролик (по умолчанию финальный)")
    rp.set_defaults(fn=cmd_review_pack)

    asp = sub.add_parser("assets", help="библиотека approved_assets")
    asp.add_argument("--export", action="store_true", help="записать assets/approved/index.yaml")
    asp.set_defaults(fn=cmd_assets)

    dr = sub.add_parser("director", help="Director Bridge (OpenAI): ping | chat | ask | review | review-scene | review-final | sync | status | export-review-package")
    dr.add_argument("action", choices=["ping", "chat", "ask", "review", "review-scene", "review-final", "sync", "status",
                                       "export-review-package"])
    dr.add_argument("episode", nargs="?")
    dr.add_argument("scene", nargs="?", help="сцена (review-scene) или текст вопроса (ask)")
    dr.add_argument("--open", action="store_true", help="(chat) открыть в браузере")
    dr.add_argument("--mock", action="store_true", help="dry-run: ответ-заглушка, без OpenAI и без денег")
    dr.add_argument("--manual", action="store_true", help="только пакет для ручного ревью в ChatGPT")
    dr.add_argument("--video", help="ролик для финального ревью (по умолчанию финальный)")
    dr.add_argument("--yes", action="store_true", help="подтвердить платный запрос к OpenAI без вопроса")
    dr.set_defaults(fn=cmd_director)

    ch = sub.add_parser("chat", help="интерактивный чат эпизода: владелец, директор (GPT), Claude")
    ch.add_argument("episode")
    ch.add_argument("--port", type=int, default=8765)
    ch.add_argument("--mock", action="store_true", help="директор-заглушка, без OpenAI")
    ch.add_argument("--no-open", action="store_true", help="не открывать браузер")
    ch.set_defaults(fn=cmd_chat)

    h = sub.add_parser("history", help="журнал действий")
    h.add_argument("episode", nargs="?")
    h.add_argument("--limit", type=int, default=50)
    h.set_defaults(fn=cmd_history)
    return p


def _utf8_console() -> None:
    """Windows: при выводе в pipe (например, из Claude Code) консоль может быть cp1251 — не падаем на эмодзи и →."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    settings = get_settings()
    try:
        return args.fn(args, settings) or 0
    except (ProjectError, ScriptError, CharacterError, BudgetError, RuntimeError, ValueError, KeyError) as e:
        print(f"Ошибка: {redact(str(e))}", file=sys.stderr)
        return 1
    except BrokenPipeError:  # вывод обрезан (например, | head) — не ошибка
        return 0
    except KeyboardInterrupt:
        print("\nПрервано. Состояние задач сохранено — продолжите командой studio status <id> --refresh")
        return 130
    except Exception as e:  # noqa: BLE001 — понятное сообщение вместо трейсбэка
        if os.environ.get("STUDIO_DEBUG"):
            raise
        print(f"Непредвиденная ошибка: {type(e).__name__}: {redact(str(e))}\n"
              "Подробности для разбора: $env:STUDIO_DEBUG=1 и повторите команду (Linux/Mac: STUDIO_DEBUG=1 studio ...)",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
