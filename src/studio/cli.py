"""CLI AI Content Studio: `studio <команда>` (или `python -m studio`)."""
from __future__ import annotations

import argparse
import shutil
import sys
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
            print(f"{r['id']:<28} {r['status']:<9} {r.get('angle', ''):<20} {r['file']}  {r.get('description', '')}")
        print("\nУтвердите референс после просмотра: studio character approve <id>")
    elif a.action == "add":
        r = lib.add(Path(a.path), a.id, kind=a.kind, angle=a.angle or "", description=a.description or "",
                    prompt=a.prompt, model=a.model)
        print(f"Добавлен {r['id']} (status: pending). Проверьте изображение и утвердите: studio character approve {r['id']}")
    elif a.action in ("approve", "reject"):
        r = lib.set_status(a.id, "approved" if a.action == "approve" else "rejected", a.note or "")
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
    proj = create_project(script, s)
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
        if dest.exists():
            dest.rename(dest.with_name(f"{a.scene}.prev-{int(dest.stat().st_mtime)}.mp4"))
        shutil.copy2(src, dest)
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


def cmd_generate(a, s):
    from .generation.runner import Runner
    proj = open_project(a.episode, s)
    Runner(proj, _db(s)).generate(_split(a.scenes), assume_yes=a.yes, regenerate=_split(a.regenerate), wait=not a.no_wait)
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
              f"${cost or 0:.3f} {j['result_path'] or ''} {('— ' + j['error'][:80]) if j['error'] else ''}")
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
    out = assemble(proj, music=Path(a.music) if a.music else None, burn_subtitles=not a.no_subs)
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
    Runner(proj, db).generate(assume_yes=a.yes)
    print("3/4 Монтаж")
    out = assemble(proj)
    print(f"   {out}")
    print("4/4 Техническая проверка")
    rep = run_qa(proj)
    save_report(proj, rep)
    proj.set_status("qa_passed" if rep.passed else "qa_failed")
    print(rep.text())
    return 0 if rep.passed else 2


def cmd_costs(a, s):
    db = _db(s)
    st = Budget(s, db).status(a.episode)
    for k, v in st.items():
        print(f"{k:<22} {v}")
    print("\nПоследние записи расходов:")
    for r in db.cost_rows(a.episode, limit=a.limit):
        print(f"  {r['ts']} {r['episode'] or '-':<40} {r['provider']:<11} {r['kind']:<9} ${r['amount_usd']:.3f} {r['note'] or ''}")
    print("\nОценочные суммы могут отличаться от фактического списания. Сверяйте с кабинетом провайдера.")
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
    c.add_argument("id", nargs="?")
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
    n.add_argument("--type", default="expert", choices=list(CONTENT_TYPES))
    n.add_argument("--duration", type=float, default=30)
    n.add_argument("--brief", help="задание обычным текстом")
    n.set_defaults(fn=cmd_new)

    sub.add_parser("list", help="проекты").set_defaults(fn=cmd_list)

    sc = sub.add_parser("script", help="сценарий: show | validate | approve")
    sc.add_argument("action", choices=["show", "validate", "approve"])
    sc.add_argument("episode")
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

    g = sub.add_parser("generate", help="генерация AI-сцен (платно, с подтверждением)")
    g.add_argument("episode")
    g.add_argument("--scenes", help="только эти сцены: s01,s03")
    g.add_argument("--regenerate", help="переделать сцены (новая платная попытка): s02")
    g.add_argument("--yes", action="store_true", help="подтвердить смету без вопроса")
    g.add_argument("--no-wait", action="store_true", help="не ждать завершения (потом: studio status --refresh)")
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

    co = sub.add_parser("costs", help="расходы и бюджет")
    co.add_argument("episode", nargs="?")
    co.add_argument("--limit", type=int, default=30)
    co.set_defaults(fn=cmd_costs)

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


if __name__ == "__main__":
    sys.exit(main())
