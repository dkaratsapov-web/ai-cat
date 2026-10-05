"""Director Bridge: обмен с контент-директором (OpenAI Responses API) без ручного копирования.

Директор только ревьюит. Он не запускает генерации, не меняет файлы и не тратит деньги:
его revision_prompt — предложение; платную перегенерацию подтверждает пользователь.
Если OpenAI недоступен — пакет сохраняется, сцена получает manual_review, ответ ChatGPT можно импортировать вручную.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..character.library import CharacterLibrary
from ..db import DB
from ..editing import ffmpeg
from ..project import Project
from . import qc, state
from .openai_client import DirectorUnavailable, MockDirector, OpenAIDirector, _image_part, build_request, output_text
from .review import write_cost_csv

KINDS = ("script_review", "scene_review", "final_review")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def load_config(settings) -> dict:
    return yaml.safe_load((settings.root / "director_bridge" / "config" / "bridge.yaml").read_text(encoding="utf-8"))


class Bridge:
    def __init__(self, project: Project, db: DB, *, mode: str = "openai"):
        """mode: openai | mock (dry-run) | manual (только пакет, без обращения к API)."""
        self.p, self.db, self.mode = project, db, mode
        self.root = project.settings.root / "director_bridge"
        self.cfg = load_config(project.settings)
        self.client = MockDirector(self.cfg) if mode == "mock" else OpenAIDirector(self.cfg)

    # ---------------------------------------------------------------- контекст
    def instructions(self) -> str:
        sp = (self.root / "prompts" / "system.md").read_text(encoding="utf-8")
        bible = (self.root / "prompts" / "character_bible.md").read_text(encoding="utf-8")
        return f"{sp}\n\n{bible}"

    def _schema(self, kind: str) -> dict:
        return json.loads((self.root / "prompts" / "schemas" / f"{kind}.json").read_text(encoding="utf-8"))

    def _notes(self) -> str:
        p = self.p.path / "director" / "director_notes.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _brief(self) -> dict:
        p = self.p.path / "director" / "brief.yaml"
        return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _conv_path(self) -> Path:
        return self.root / "conversations" / f"{self.p.id}.json"

    def _conv(self) -> dict:
        p = self._conv_path()
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"episode": self.p.id, "last_response_id": None,
                                                                             "history": []}

    # ---------------------------------------------------------------- обмен
    def _exchange(self, kind: str, text: str, images: list[Path], package: Path, scene: str = "") -> dict | None:
        """Отправка в OpenAI (или mock). None — директор недоступен: включён ручной режим."""
        conv = self._conv()
        body = build_request(self.cfg, instructions=self.instructions(), text=text, images=images,
                             schema_name=kind, schema=self._schema(kind),
                             previous_response_id=conv.get("last_response_id") if self.mode == "openai" else None)
        # Запрос сохраняем без картинок (base64) — для аудита
        slim = json.loads(json.dumps(body))
        for c in slim["input"][0]["content"]:
            if c["type"] == "input_image":
                c["image_url"] = c["image_url"][:40] + "…"
        (package / "request.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8")
        if self.mode == "manual":
            self._manual_hint(package, kind, scene)
            return None
        try:
            resp = self.client.send(body)
        except DirectorUnavailable as e:
            print(f"Директор недоступен: {e}. Пакет сохранён — ревью вручную.")
            self._manual_hint(package, kind, scene)
            return None
        data = json.loads(output_text(resp) or "{}")
        rdir = self.root / "responses" / self.p.id
        rdir.mkdir(parents=True, exist_ok=True)
        rec = {"kind": kind, "scene": scene, "response_id": resp.get("id"), "model": resp.get("model"),
               "usage": resp.get("usage"), "at": _now(), "mode": self.mode, "review": data}
        (rdir / f"{_now()}_{kind}{('_' + scene) if scene else ''}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        (package / "response.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        if self.mode == "openai":   # отдельный контекст на каждый эпизод
            conv["last_response_id"] = resp.get("id")
        conv["history"].append({"kind": kind, "scene": scene, "response_id": resp.get("id"), "at": rec["at"], "mode": self.mode})
        self._conv_path().parent.mkdir(parents=True, exist_ok=True)
        self._conv_path().write_text(json.dumps(conv, ensure_ascii=False, indent=1), encoding="utf-8")
        with open(self.root / "logs" / "bridge.log", "a", encoding="utf-8") as f:
            f.write(f"{rec['at']} {self.p.id} {kind} {scene or '-'} {self.mode} {resp.get('id')} {data.get('status')}\n")
        return data

    def _kb(self, query: str) -> str:
        from ..knowledge import search
        hits = search(self.p.settings, query, limit=5)
        return "\n\n".join(f"[{h['section']}] {h['path']}:\n{h['snippet']}" for h in hits) or "(ничего не найдено)"

    def _visuals(self) -> tuple[list[Path], str]:
        """Что директор видит при аудите: готовый ролик (контактный лист + ключевые кадры, субтитры, карта сцен),
        а если ролика ещё нет — раскадровку по кадрам сцен."""
        video = self.p.final_video
        if video.exists():
            pkg = self._pkg("audit")
            export_final_package(self.p, self.db, pkg, video)
            imgs = [pkg / "contact_sheet.jpg"] + sorted((pkg / "keyframes").glob("*.jpg"))[:8]
            srt = (pkg / "subtitles.srt").read_text(encoding="utf-8") if (pkg / "subtitles.srt").exists() else ""
            sm = (pkg / "scene_map.json").read_text(encoding="utf-8") if (pkg / "scene_map.json").exists() else ""
            return [i for i in imgs if i.exists()], (f"ВИЗУАЛ: собранный ролик — контактный лист (кадр каждые 2.5 с с "
                                                     f"таймкодами) и ключевые кадры.\nКАРТА СЦЕН:\n{sm}\nСУБТИТРЫ:\n{srt[:6000]}")
        script = self.p.load_script()
        lib = CharacterLibrary(self.p.settings)
        refs, labels = [], []
        for sc in script.scenes:
            if sc.reference:
                try:
                    _r, path = lib.resolve(sc.reference)
                    refs.append(Path(path)); labels.append(sc.id)
                except Exception:  # noqa: BLE001
                    pass
        if not refs:
            return [], "ВИЗУАЛ: ролик ещё не собран, кадров нет."
        sheet = qc.sheet(refs, labels, self._pkg("audit") / "storyboard_sheet.jpg", cols=5, cell_w=220)
        return [sheet], "ВИЗУАЛ: ролик ещё не собран — раскадровка (исходный кадр каждой сцены)."

    def ask(self, question: str, *, log_question: bool = True, audit: bool = False) -> str | None:
        """Свободный вопрос владельца директору в той же переписке эпизода. Ответ — обычный текст.
        audit=True: директор первым смотрит работу (кадры) и даёт аудит + приоритетный список правок для Claude."""
        from . import chat
        if log_question:
            chat.append(self.p, "owner", question)
        conv = self._conv()
        stall = state.sync_from_jobs(self.p, self.db)   # статусы есть и у проектов, созданных не из брифа
        script = self.p.load_script()
        scenes = [{"id": sc.id, "type": sc.type, "generator": sc.generator, "reference": sc.reference,
                   "voiceover": sc.voiceover, "status": (stall.get(sc.id) or {}).get("status"),
                   "version": (stall.get(sc.id) or {}).get("versions", 0),
                   "director": ((stall.get(sc.id) or {}).get("director") or {}).get("status"),
                   "identity_qc": ((stall.get(sc.id) or {}).get("identity_qc") or {}).get("verdict")}
                  for sc in script.scenes]
        if not (self.p.path / "director" / "director_notes.md").exists():   # проект не из брифа — заводим заметки
            from .brief import init_director_notes
            init_director_notes(self.p, {"project": self.p.id, "goal": {"audience": script.audience, "cta": script.cta,
                                                                        "objective": script.topic}})
        notes = self._notes()
        images: list[Path] = []
        visual = ""
        if audit:
            images, visual = self._visuals()
            head = (f"ЗАДАЧА ВЛАДЕЛЬЦА: {question}\n\nТы смотришь работу ПЕРВЫМ, до продюсера (Claude). Отвечай обычным "
                    "текстом по-русски, НЕ JSON. Дай аудит: 1) что работает; 2) проблемы — с таймкодами/сценами и "
                    "почему это вредит удержанию; 3) приоритетный список правок для продюсера, отдельно бесплатные "
                    "(монтаж, текст, плашки, тайминг) и платные (перегенерация сцен). Опирайся на кадры, не выдумывай.\n\n"
                    f"{visual}\n\n")
        else:
            head = (f"ВОПРОС ВЛАДЕЛЬЦА: {question}\n\nЭто свободный вопрос: отвечай обычным текстом по-русски, НЕ JSON, "
                    f"коротко и по делу. Если данных не хватает — скажи, каких именно. Платные действия только предлагай, "
                    f"решает владелец.\n\n")
        text = (head + f"ЭПИЗОД: {script.title}, CTA: {script.cta}\nСЦЕНЫ И СТАТУСЫ:\n```json\n"
                f"{json.dumps(scenes, ensure_ascii=False)}\n```\n\nDIRECTOR NOTES:\n{notes}\n\n"
                f"ПОСЛЕДНИЕ СООБЩЕНИЯ ЧАТА (владелец, директор, Claude-продюсер):\n{chat.recent_text(self.p, 12)}"
                f"\n\nБАЗА ЗНАНИЙ — фрагменты по теме вопроса (опирайся на них, не выдумывай):\n{self._kb(question)}")
        body = {"model": self.cfg["model"], "instructions": self.instructions(),
                "input": [{"role": "user", "content": [{"type": "input_text", "text": text}] +
                           [_image_part(i, self.cfg.get("image_detail", "auto")) for i in images[: int(self.cfg.get("max_images", 12))]]}],
                "store": bool(self.cfg.get("store", True)), "max_output_tokens": int(self.cfg.get("max_output_tokens", 4000))}
        if self.mode == "openai" and conv.get("last_response_id"):
            body["previous_response_id"] = conv["last_response_id"]
        try:
            resp = self.client.send(body)
        except DirectorUnavailable as e:
            print(f"Директор недоступен: {e}")
            self.last_error = str(e)[:300]
            return None
        answer = output_text(resp).strip()
        try:   # модель могла по привычке ответить JSON-ом — достаём текст
            j = json.loads(answer)
            if isinstance(j, dict):
                answer = "\n".join(str(v) for k, v in j.items() if v and k != "requires_user_approval")
        except ValueError:
            pass
        chat.append(self.p, "director", answer + (" (заглушка)" if self.mode == "mock" else ""),
                    response_id=resp.get("id"))
        if self.mode == "openai":
            conv["last_response_id"] = resp.get("id")
        conv["history"].append({"kind": "audit" if audit else "ask", "scene": "", "response_id": resp.get("id"), "at": _now(), "mode": self.mode})
        self._conv_path().parent.mkdir(parents=True, exist_ok=True)
        self._conv_path().write_text(json.dumps(conv, ensure_ascii=False, indent=1), encoding="utf-8")
        return answer

    def _manual_hint(self, package: Path, kind: str, scene: str) -> None:
        if kind == "scene_review" and scene:
            st = state.load(self.p).get(scene, {})
            if st.get("status") in ("generated", "review", "revise"):
                state.set_status(self.p, scene, "manual_review", "директор недоступен — ручное ревью", force=True)
        (package / "MANUAL.md").write_text(
            "# Ручное ревью\n\nOpenAI недоступен или включён ручной режим. Загрузите файлы этой папки в ChatGPT "
            "(вместе с `request.json` — там текст запроса и схема ответа), получите JSON-ответ и импортируйте:\n\n"
            f"`studio review import {self.p.id} {scene or kind.split('_')[0]} --file ответ.json`\n", encoding="utf-8")
        print(f"Пакет для ручного ревью: {package}")

    # ---------------------------------------------------------------- 6.1 сценарий
    def script_review(self) -> dict | None:
        script = self.p.load_script()
        pkg = self._pkg("script")
        lib = CharacterLibrary(self.p.settings)
        refs, labels = [], []
        meta = {"title": script.title, "goal": self._brief().get("goal", {}), "audience": script.audience,
                "target_duration": script.target_duration, "planned_duration": script.planned_duration, "scenes": []}
        for sc in script.scenes:
            vm = (self.p.dir("audio") / f"{sc.id}.json")
            meta["scenes"].append({"id": sc.id, "type": sc.type, "location": sc.local.get("location", ""),
                                   "asset": sc.reference, "generator": sc.generator, "voiceover": sc.voiceover,
                                   "subtitle": sc.subtitle_text, "duration_plan": sc.duration,
                                   "overlays": [o.get("text") or [i.get("label") for i in o.get("items") or []]
                                                for o in sc.local.get("overlays") or []]})
            if sc.reference:
                try:
                    _r, path = lib.resolve(sc.reference)
                    refs.append(Path(path)); labels.append(sc.id)
                except Exception:  # noqa: BLE001
                    pass
        images = []
        if refs:
            images.append(qc.sheet(refs, labels, pkg / "storyboard_sheet.jpg", cols=5, cell_w=220))
        (pkg / "script.yaml").write_text(self.p.script_path.read_text(encoding="utf-8"), encoding="utf-8")
        text = ("SCRIPT REVIEW. Проверь сценарий и раскадровку (лист: кадр каждой сцены). Учитывай director_notes.\n\n"
                f"```json\n{json.dumps(meta, ensure_ascii=False, indent=1)}\n```\n\nDIRECTOR NOTES:\n{self._notes()}")
        data = self._exchange("script_review", text, images, pkg)
        if data:
            st = state.load(self.p)
            for n in data.get("scene_notes", []):
                st.setdefault(n["scene_id"], {"status": "approved", "history": [], "identity_qc": None, "versions": 0})
                st[n["scene_id"]]["script_review"] = {"status": n["status"], "notes": n["notes"]}
            state.save(self.p, st)
            self._write_md("latest_review.md", "Script review", data)
        return data

    # ---------------------------------------------------------------- 6.2 сцена
    def scene_review(self, sid: str) -> dict | None:
        state.sync_from_jobs(self.p, self.db)
        script = self.p.load_script()
        sc = next(s for s in script.scenes if s.id == sid)
        src = self.p.scene_source(sid)
        if not src:
            raise state.StateError(f"{sid}: нет видео сцены")
        st = state.load(self.p).get(sid, {})
        pkg = self._pkg(f"{sid}_v{st.get('versions', 0)}")
        info = qc.prepare_identity_qc(self.p, sid)
        _r, ref_path = CharacterLibrary(self.p.settings).resolve(sc.reference)
        shutil.copy2(ref_path, pkg / "source.png" if str(ref_path).endswith(".png") else pkg / "source.jpg")
        shutil.copy2(src, pkg / "generated.mp4")
        shutil.copy2(info["sheet"], pkg / "identity_sheet.jpg")
        jobs = [j for j in self.db.jobs_for(self.p.id, sid) if j["kind"] != "tts" and j["status"] in ("succeeded", "imported")]
        job = jobs[-1] if jobs else {}
        prev = (self.p.dir("scenes") / sid / "review.md")
        meta = {"scene_id": sid, "type": sc.type, "location": sc.local.get("location", ""), "asset": sc.reference,
                "prompt": (sc.prompts.get(sc.generator) or ""), "duration": round(ffmpeg.duration(src), 2),
                "provider": job.get("provider"), "model": job.get("model"),
                "cost_usd": job.get("actual_cost_usd") or job.get("est_cost_usd"), "version": st.get("versions"),
                "voiceover": sc.voiceover, "fur_tone_similarity": info["similarity"],
                "review_rules": self._brief().get("review_rules", {})}
        (pkg / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        text = ("SCENE REVIEW. Первое изображение — утверждённый эталон кота; второе — контактный лист (эталон + 4 кадра "
                "сгенерированного клипа). Видео приложить нельзя — оцени по кадрам. Проверь идентичность по character bible.\n\n"
                f"```json\n{json.dumps(meta, ensure_ascii=False, indent=1)}\n```\n\nPREVIOUS REVIEW NOTES:\n"
                f"{prev.read_text(encoding='utf-8') if prev.exists() else '(нет)'}\n\nDIRECTOR NOTES:\n{self._notes()}")
        images = [Path(ref_path), Path(info["sheet"])] + [Path(f) for f in info["frames"]]
        data = self._exchange("scene_review", text, images, pkg, scene=sid)
        if data:
            self._store_scene_review(sid, data)
        return data

    def _store_scene_review(self, sid: str, data: dict) -> None:
        stall = state.load(self.p)
        st = stall.setdefault(sid, {"status": "generated", "history": [], "identity_qc": None, "versions": 0})
        st["director"] = {"status": data.get("status"), "version": st.get("versions"), "at": _now(),
                          "revision_prompt": data.get("revision_prompt", ""), "issues": data.get("issues", [])}
        idn = data.get("identity") or {}
        # Низкая/средняя уверенность директора → ручной QC; вердикт pass только при высокой уверенности
        verdict = "fail" if not idn.get("pass") else ("pass" if idn.get("confidence") == "high" else "manual")
        st["identity_qc"] = {"verdict": verdict, "notes": idn.get("notes", ""), "reviewer": "director",
                             "version": st.get("versions"), "at": _now()}
        state.save(self.p, stall)
        if st["status"] in ("generated", "manual_review"):
            state.set_status(self.p, sid, "review", "ревью директора получено", force=True)
        rdir = self.p.dir("scenes") / sid
        rdir.mkdir(parents=True, exist_ok=True)
        with open(rdir / "review.md", "a", encoding="utf-8") as f:
            f.write(self._md(f"Director review {sid} v{st.get('versions')}", data))
        self._write_md("latest_review.md", f"Scene review {sid}", data)

    # ---------------------------------------------------------------- 6.3 финал
    def final_review(self, video: Path | None = None) -> dict | None:
        video = video or self.p.final_video
        if not video.exists():
            raise state.StateError(f"Нет ролика {video.name}: сначала studio assemble {self.p.id}")
        pkg = self._pkg("final")
        export_final_package(self.p, self.db, pkg, video)
        sm = json.loads((pkg / "scene_map.json").read_text(encoding="utf-8"))
        text = ("FINAL VIDEO REVIEW. Контактный лист: кадр каждые 2.5 с с таймкодами. Ниже карта сцен, субтитры, сценарий, "
                "музыка/переходы. Дай оценку и точные таймкоды правок.\n\n"
                f"```json\n{json.dumps(sm, ensure_ascii=False, indent=1)}\n```\n\nSUBTITLES (SRT):\n"
                f"{(pkg / 'subtitles.srt').read_text(encoding='utf-8') if (pkg / 'subtitles.srt').exists() else ''}\n\n"
                f"DIRECTOR NOTES:\n{self._notes()}")
        images = [pkg / "contact_sheet.jpg"] + sorted((pkg / "keyframes").glob("*.jpg"))[:10]
        data = self._exchange("final_review", text, images, pkg, scene="final")
        if data:
            self._write_md("final_review.md", "Final review", data)
            self._write_md("latest_review.md", "Final review", data)
        return data

    # ---------------------------------------------------------------- служебное
    def _pkg(self, name: str) -> Path:
        p = self.root / "review_packages" / self.p.id / f"{_now()}_{name}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _md(self, title: str, data: dict) -> str:
        return f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} — {title}\n\n```yaml\n" + \
               yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=110) + "```\n"

    def _write_md(self, name: str, title: str, data: dict) -> None:
        d = self.p.path / "director"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(f"# {title} — {self.p.id}\n" + self._md(title, data), encoding="utf-8")


def export_final_package(project: Project, db: DB, pkg: Path, video: Path) -> Path:
    """Пакет финального ревью: preview.mp4, contact_sheet.jpg (кадр каждые 2.5 с), keyframes/, scene_map.json,
    subtitles.srt, script.txt, director_brief.yaml, costs.csv."""
    import subprocess
    pkg.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vf", "scale=540:-2",
                    "-c:v", "libx264", "-crf", "27", "-preset", "veryfast", "-c:a", "aac", "-b:a", "128k",
                    str(pkg / "preview.mp4")], check=False)
    dur = ffmpeg.duration(video)
    kf = pkg / "keyframes"
    frames, labels = [], []
    t = 1.0
    while t < dur:
        f = qc.frame_at(video, t, kf / f"t{t:05.1f}.jpg", width=270)
        if f:
            frames.append(f); labels.append(f"{int(t // 60):02d}:{t % 60:04.1f}")
        t += 2.5
    if frames:
        qc.sheet(frames, labels, pkg / "contact_sheet.jpg", cols=6, cell_w=200)
    tl_path = project.dir("output") / "timeline.json"
    timeline = json.loads(tl_path.read_text(encoding="utf-8")) if tl_path.exists() else []
    script = project.load_script()
    by_id = {s.id: s for s in script.scenes}
    st = state.load(project)
    scene_map = {"episode": project.id, "duration": round(dur, 2), "music": project.settings.get("music.default_track"),
                 "scenes": [{"id": it["scene"], "start": it["start"], "end": round(it["start"] + it["duration"], 2),
                             "type": it["type"], "generator": it["generator"],
                             "location": by_id[it["scene"]].local.get("location", "") if it["scene"] in by_id else "",
                             "transition_in": by_id[it["scene"]].local.get("transition", "fade") if it["scene"] in by_id else "",
                             "status": (st.get(it["scene"]) or {}).get("status", ""),
                             "identity_qc": ((st.get(it["scene"]) or {}).get("identity_qc") or {}).get("verdict", "")}
                            for it in timeline]}
    (pkg / "scene_map.json").write_text(json.dumps(scene_map, ensure_ascii=False, indent=1), encoding="utf-8")
    srt = project.dir("subtitles") / f"{project.id}.srt"
    if srt.exists():
        shutil.copy2(srt, pkg / "subtitles.srt")
    (pkg / "script.txt").write_text("\n".join(f"{s.id}: {s.subtitle_text}" for s in script.scenes), encoding="utf-8")
    brief = project.path / "director" / "brief.yaml"
    if brief.exists():
        shutil.copy2(brief, pkg / "director_brief.yaml")
    write_cost_csv(db, pkg / "costs.csv", project.id)
    return pkg


def import_review(project: Project, db: DB, target: str, file: Path) -> dict:
    """Ответ ChatGPT, полученный вручную (fail-safe): JSON или ```json-блок внутри markdown."""
    raw = file.read_text(encoding="utf-8")
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0]
    data = json.loads(raw)
    from . import chat
    chat.append(project, "director", "[ручной ответ из ChatGPT]\n" + chat._review_text(
        "script_review" if target == "script" else ("final_review" if target == "final" else "scene_review"),
        target, data), scene=target if target not in ("script", "final") else "")
    b = Bridge(project, db, mode="manual")
    if target == "script":
        b._write_md("latest_review.md", "Script review (manual)", data)
    elif target == "final":
        b._write_md("final_review.md", "Final review (manual)", data)
    else:
        b._store_scene_review(target, data)
    return data
