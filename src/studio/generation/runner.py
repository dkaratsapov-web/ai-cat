"""Планирование и выполнение генераций сцен.

Гарантии:
  * без утверждённого сценария платные задачи не создаются;
  * смета показывается и подтверждается до отправки;
  * одинаковая задача (тот же провайдер/модель/промпт/кадр/аудио) не оплачивается дважды —
    уже готовый или выполняющийся результат переиспользуется;
  * запись о задаче создаётся ДО запроса, task_id сохраняется сразу после ответа;
  * при обрыве связи во время отправки задача ищется по external_task_id, а не отправляется заново;
  * число платных попыток на сцену ограничено (budget.max_attempts_per_scene).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from ..character.library import CharacterLibrary
from ..config import Settings, redact
from ..costs.budget import Budget, CostLine, Estimate
from ..db import ACTIVE_STATUSES, DB, DONE_STATUSES
from ..integrations import video_provider
from ..integrations.base import AmbiguousSubmitError, ProviderError, VideoRequest
from ..models import Scene, Script
from ..project import Project
from .voice import scene_voice_meta


class GenerationError(RuntimeError):
    pass


@dataclass
class PlannedJob:
    scene: Scene
    provider: str
    request: VideoRequest
    key: str

    @property
    def kind(self) -> str:
        return self.request.kind


def file_hash(p: Path | None) -> str:
    if not p or not Path(p).exists():
        return ""
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


def scene_start_image(project: Project, scene: Scene, lib: CharacterLibrary) -> Path:
    """Первый кадр для AI-сцены: собственный (local.image) или вертикальный кадр утверждённого референса."""
    if scene.local.get("image"):
        from ..editing.local_scenes import resolve_image
        return resolve_image(project.path, project.settings, scene.local["image"])
    ref, src = lib.resolve(scene.reference)
    focus = tuple(ref.get("focus", [0.6, 0.45]))
    dest = project.dir("images") / f"ref_{ref['id']}_9x16.jpg"
    if not dest.exists() or dest.stat().st_mtime < src.stat().st_mtime:
        lib.vertical_frame(src, dest, focus=focus)  # type: ignore[arg-type]
    return dest


def plan_scene(project: Project, scene: Scene, lib: CharacterLibrary, *, need_audio: bool = True) -> PlannedJob | None:
    s = project.settings
    gen = scene.generator
    if gen in ("local", "manual"):
        return None
    image = scene_start_image(project, scene, lib)
    prompt_raw = scene.prompts.get(gen) or scene.prompts.get("kling") or scene.visual
    prompt = lib.build_prompt(prompt_raw) if scene.needs_character else prompt_raw
    negative = lib.negative() if scene.needs_character else ""
    audio = None
    meta = scene_voice_meta(project, scene.id)
    talking = scene.type == "talking" and scene.wants_lipsync
    if talking:
        if meta:
            audio = project.scene_audio(scene.id)
        elif need_audio:
            raise GenerationError(f"{scene.id}: для говорящей сцены сначала нужна озвучка (studio voice {project.id})")
    voice_dur = meta["duration"] if meta else None
    if talking:
        duration = scene.clip_duration or (voice_dur + 0.3 if voice_dur else scene.duration)
    else:
        # Сцены без речи: короткий (самый дешёвый) клип, остальное — локальное продление «туда-обратно»
        duration = scene.clip_duration or min(scene.duration, 5)

    # Тестовый режим: все платные генераторы подменяются бесплатной заглушкой
    if s.get("override_generator") and gen in ("kling", "higgsfield", "hedra", "runway"):
        gen = s.get("override_generator")
    provider = gen
    if talking:
        talking_mode = scene.local.get("talking_mode") or s.get("kling.talking_mode", "avatar")
        if gen in ("kling", "mock") and talking_mode == "motion_lipsync":
            # Живая анимация всего кадра (Kling 2.6) + синхронизация губ с озвучкой (Kling Lip Sync)
            req = VideoRequest(kind="motion_lipsync",
                               model=scene.local.get("model") or s.get("kling.i2v_model", "kling-2.6"),
                               prompt=prompt, negative_prompt=negative, image=image, audio=audio,
                               duration=round(duration, 2),
                               resolution=scene.local.get("resolution") or s.get("kling.resolution", "720p"),
                               extra={"audio_seconds": round(voice_dur or scene.duration, 2)})
        elif gen in ("kling", "mock"):
            req = VideoRequest(kind="avatar", model="avatar", prompt=prompt, image=image, audio=audio,
                               duration=round(duration, 2), mode=scene.local.get("mode") or s.get("kling.avatar_mode", "std"))
        elif gen == "higgsfield":
            # Экспериментально: говорящее видео Higgsfield Speak (фото + WAV). На коте не проверено
            req = VideoRequest(kind="avatar", model=s.get("higgsfield.speak_model", "v1/speak/higgsfield"),
                               prompt=prompt, image=image, audio=audio, duration=round(duration, 2),
                               mode=scene.local.get("quality") or s.get("higgsfield.speak_quality", "mid"),
                               resolution="default")
        elif gen == "hedra":
            req = VideoRequest(kind="avatar", model=s.get("hedra.model", "hedra-character-3"), prompt=prompt,
                               image=image, audio=audio, duration=round(duration, 2),
                               resolution=s.get("hedra.resolution", "720p"))
        else:
            raise GenerationError(f"{scene.id}: генератор {gen} не умеет говорящего персонажа — используйте kling или hedra")
    else:
        if gen in ("kling", "mock"):
            req = VideoRequest(kind="image2video", model=scene.local.get("model") or s.get("kling.i2v_model", "kling-2.6"),
                               prompt=prompt, negative_prompt=negative, image=image, duration=duration,
                               resolution=scene.local.get("resolution") or s.get("kling.resolution", "720p"))
        elif gen == "higgsfield":
            req = VideoRequest(kind="image2video",
                               model=scene.local.get("model") or s.get("higgsfield.i2v_model",
                                                                       "bytedance/seedance-2.5/image-to-video"),
                               prompt=prompt, negative_prompt=negative, image=image, duration=duration,
                               resolution=scene.local.get("resolution") or s.get("higgsfield.resolution", "720p"))
        elif gen == "runway":
            req = VideoRequest(kind="image2video", model=s.get("runway.model", "gen4_turbo"), prompt=prompt, image=image,
                               duration=duration, extra={"ratio": s.get("runway.ratio", "720:1280")})
        else:
            raise GenerationError(f"{scene.id}: генератор {gen} не поддерживает {scene.type}")

    prov = video_provider(provider, s)
    billed = prov.billed_duration(req)
    key_src = {
        "provider": provider, "kind": req.kind, "model": req.model, "res": req.resolution, "mode": req.mode,
        "dur": billed, "prompt": req.prompt, "neg": req.negative_prompt, "img": file_hash(image),
        "audio": file_hash(audio), "extra": req.extra,
    }
    key = hashlib.sha256(json.dumps(key_src, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    return PlannedJob(scene=scene, provider=provider, request=req, key=key)


def plan(project: Project, script: Script, scene_ids: list[str] | None = None, need_audio: bool = True) -> list[PlannedJob]:
    lib = CharacterLibrary(project.settings)
    jobs = []
    for sc in script.scenes:
        if scene_ids and sc.id not in scene_ids:
            continue
        pj = plan_scene(project, sc, lib, need_audio=need_audio)
        if pj:
            jobs.append(pj)
    return jobs


def existing(db: DB, key: str) -> dict | None:
    """Последняя задача с тем же ключом, которая готова или ещё выполняется."""
    rows = [r for r in db.find_by_key(key) if r["status"] in DONE_STATUSES + ACTIVE_STATUSES]
    return rows[-1] if rows else None


def estimate(project: Project, jobs: list[PlannedJob], db: DB) -> Estimate:
    pricing = project.settings.load_yaml("config/pricing.yaml")
    est = Estimate(episode=project.id)
    for j in jobs:
        prov = video_provider(j.provider, project.settings)
        usd = prov.estimate_usd(j.request, pricing)
        billed = prov.billed_duration(j.request)
        detail = f"{j.request.model} {j.request.resolution if j.kind != 'avatar' else j.request.mode} {billed:g}с"
        est.lines.append(CostLine(j.scene.id, j.provider, j.kind, j.request.model, detail, usd,
                                  reused=existing(db, j.key) is not None,
                                  verified_price=bool(pricing.get(j.provider, {}).get("verified", True))))
    return est


class Runner:
    def __init__(self, project: Project, db: DB):
        self.p = project
        self.s: Settings = project.settings
        self.db = db

    # ------------------------------------------------------------------ public
    def generate(self, scene_ids: list[str] | None = None, *, assume_yes: bool = False,
                 regenerate: list[str] | None = None, wait: bool = True) -> list[dict]:
        script = self.p.require_approved()
        jobs = plan(self.p, script, scene_ids)
        if not jobs:
            print("Платных/AI-сцен нет — все сцены собираются локально.")
            return []
        regenerate = regenerate or []
        pricing = self.s.load_yaml("config/pricing.yaml")
        to_submit: list[PlannedJob] = []
        reused: list[dict] = []
        for j in jobs:
            ex = existing(self.db, j.key)
            if ex and ex["status"] not in DONE_STATUSES:
                # Задача ещё идёт или её судьба неясна — новая отправка (даже с --regenerate) = риск двойной оплаты
                if j.scene.id in regenerate:
                    hint = (f"studio jobs resolve {ex['id'][:8]} --status failed|cancelled" if ex["status"] == "unknown"
                            else f"studio status {self.p.id} --refresh")
                    print(f"{j.scene.id}: предыдущая задача в статусе {ex['status']} — переделка заблокирована, "
                          f"чтобы не заплатить дважды. Сначала: {hint}")
                if ex["status"] != "unknown":
                    reused.append(ex)
                continue
            if ex and j.scene.id not in regenerate:
                self._ensure_local(ex, j.scene.id)
                continue
            failed = [r for r in self.db.find_by_key(j.key) if r["status"] == "failed"]
            if failed and j.scene.id not in regenerate:
                print(f"{j.scene.id}: прошлая попытка завершилась ошибкой ({failed[-1]['error']}). "
                      f"Повтор только явно: --regenerate {j.scene.id}")
                continue
            to_submit.append(j)

        if to_submit:
            est = estimate(self.p, to_submit, self.db)
            for l in est.lines:
                l.reused = False
            print(est.table())
            prov_paid = any(video_provider(j.provider, self.s).paid for j in to_submit)
            if prov_paid:
                budget = Budget(self.s, self.db)
                for w in budget.check(self.p.id, est.total):
                    print(w)
                for j in to_submit:
                    attempts = self.db.paid_attempts(self.p.id, j.scene.id, j.kind)
                    limit = int(self.s.get("budget.max_attempts_per_scene", 3))
                    if attempts >= limit:
                        raise GenerationError(f"{j.scene.id}: исчерпан лимит платных попыток ({attempts}/{limit}). "
                                              "Измените сцену или увеличьте budget.max_attempts_per_scene.")
                if not budget.confirm(f"Будет запущено {len(to_submit)} платных генераций на ~${est.total:.2f}.",
                                      assume_yes):
                    print("Отменено. Ничего не отправлено.")
                    return []

        submitted = []
        for j in to_submit:
            try:
                submitted.append(self._submit(j, pricing))
            except GenerationError as e:
                print(f"{j.scene.id}: {redact(str(e))}")
        all_jobs = [r for r in reused + submitted if r]
        if wait:
            self.wait([r["id"] for r in all_jobs])
        self._update_project_status(script)
        return [self.db.get_job(r["id"]) for r in all_jobs]  # type: ignore[misc]

    def refresh(self, download: bool = True) -> list[dict]:
        """Один проход опроса всех незавершённых задач эпизода (для `studio status`)."""
        out = []
        for job in self.db.active_jobs(self.p.id):
            if job["kind"] == "tts":  # озвучка синхронная, опрашивать нечего
                continue
            out.append(self._poll_once(job, download=download))
        seen: set[str] = set()
        for job in reversed(self.db.jobs_for(self.p.id)):  # новые первыми — не возвращаем отменённую версию
            if job["kind"] == "tts" or job["status"] not in DONE_STATUSES or job["scene_id"] in seen:
                continue
            seen.add(job["scene_id"])  # ручной импорт тоже «последняя версия» — старое поверх него не качаем
            if job["status"] == "succeeded" and download and not self._result_file(job).exists():
                self._download(job)
        return out

    def wait(self, job_ids: list[str]) -> None:
        interval = int(self.s.get("generation.poll_interval_sec", 10))
        deadline = time.time() + int(self.s.get("generation.poll_timeout_sec", 1800))
        pending = set(job_ids)
        while pending and time.time() < deadline:
            for jid in list(pending):
                job = self.db.get_job(jid)
                if not job:
                    pending.discard(jid)
                    continue
                if job["status"] in ACTIVE_STATUSES and job["status"] != "unknown":
                    job = self._poll_once(job)
                if job["status"] in DONE_STATUSES and not self._result_file(job).exists():
                    self._download(job)
                if job["status"] not in ACTIVE_STATUSES or job["status"] == "unknown":
                    pending.discard(jid)
                    print(f"  {job['scene_id']}: {job['status']}" + (f" — {job['error']}" if job.get("error") else ""))
            if pending:
                time.sleep(interval)
        if pending:
            print(f"Не дождались {len(pending)} задач. Состояние сохранено — продолжите: studio status {self.p.id} --refresh")

    # ------------------------------------------------------------------ internals
    def _result_file(self, job: dict) -> Path:
        suffix = ".mp4"
        return self.p.dir("scenes") / f"{job['scene_id']}{suffix}"

    def _submit(self, j: PlannedJob, pricing: dict) -> dict | None:
        prov = video_provider(j.provider, self.s)
        ok, why = prov.configured()
        if not ok:
            raise GenerationError(f"{j.provider} не настроен: {why}")
        attempt = len(self.db.find_by_key(j.key)) + 1
        est_usd = prov.estimate_usd(j.request, pricing)
        job_id = self.db.create_job(
            episode=self.p.id, scene_id=j.scene.id, provider=j.provider, kind=j.kind, model=j.request.model,
            params={**j.request.describe(), "billed_duration": prov.billed_duration(j.request)},
            idempotency_key=j.key, est_cost_usd=est_usd, attempt=attempt, paid=prov.paid, status="submitting",
        )
        j.request.external_id = job_id
        try:
            task_id = prov.submit(j.request)
        except (AmbiguousSubmitError, requests.RequestException) as e:
            # Запрос мог дойти до сервера: ищем задачу по нашему id, повторно НЕ отправляем
            found = prov.find_by_external_id(job_id, j.kind)
            if found:
                status = "failed" if found.status == "failed" else "processing"  # дальше уточнит опрос
                self.db.update_job(job_id, status=status, external_task_id=found.task_id)
            else:
                self.db.update_job(job_id, status="unknown", error=str(e))
                print(f"{j.scene.id}: неясно, создана ли задача ({redact(str(e))}). Повторная отправка заблокирована. "
                      f"Проверьте кабинет {j.provider} и выполните: studio jobs resolve {job_id[:8]} --status failed|cancelled")
            return self.db.get_job(job_id)
        except ProviderError as e:
            # Сервер явно отказал — задача не создана, деньги не списаны
            self.db.update_job(job_id, status="failed", error=str(e), paid=0)
            print(f"{j.scene.id}: отказ {j.provider}: {redact(str(e))}")
            return None
        except Exception as e:  # noqa: BLE001 — локальный сбой до отправки (ffmpeg, файл): запрос не уходил
            self.db.update_job(job_id, status="failed", error=f"локальная ошибка до отправки: {e}", paid=0)
            print(f"{j.scene.id}: локальная ошибка подготовки запроса, ничего не отправлено: {redact(str(e))}")
            return None
        self.db.update_job(job_id, status="submitted", external_task_id=task_id)
        if prov.paid:
            self.db.add_cost(amount_usd=est_usd, kind="estimated", provider=j.provider, episode=self.p.id,
                             job_id=job_id, note=f"{j.kind} {j.request.model} {j.scene.id}")
        self.db.log("submit", {"scene": j.scene.id, "provider": j.provider, "task": task_id, "usd": est_usd},
                    episode=self.p.id)
        print(f"  {j.scene.id}: отправлено в {j.provider} (task {task_id})")
        return self.db.get_job(job_id)

    def _poll_once(self, job: dict, download: bool = True) -> dict:
        prov = video_provider(job["provider"], self.s)
        if job["status"] in ("submitting", "unknown") and not job.get("external_task_id"):
            found = prov.find_by_external_id(job["id"], job["kind"])
            if not found:
                if job["status"] == "submitting":
                    self.db.update_job(job["id"], status="unknown",
                                       error="задача не найдена у провайдера после сбоя — проверьте кабинет")
                return self.db.get_job(job["id"])  # type: ignore[return-value]
            self.db.update_job(job["id"], external_task_id=found.task_id, status="processing")
            job = self.db.get_job(job["id"])  # type: ignore[assignment]
        try:
            ctx = {**json.loads(job.get("params_json") or "{}"), "job_id": job["id"]}
            st = prov.poll(job["external_task_id"], job["kind"], ctx)
        except ProviderError as e:
            print(f"  {job['scene_id']}: ошибка опроса ({redact(str(e))}) — попробуем позже")
            return job
        fields: dict = {"status": st.status if st.status in ("succeeded", "failed") else "processing"}
        if st.next_task_id:  # следующий шаг цепочки (lip-sync) — сохраняем сразу, чтобы не отправить повторно
            fields["external_task_id"] = st.next_task_id
        if st.video_url:
            fields["result_url"] = st.video_url
        if st.message and st.status == "failed":
            fields["error"] = st.message
        if st.message and st.status == "succeeded":
            fields["error"] = f"предупреждение: {st.message}"
            print(f"  {job['scene_id']}: {st.message}")
        if (st.billed_units is not None and job["provider"] == "kling" and st.status in ("succeeded", "failed")
                and job.get("actual_cost_usd") is None):
            unit = float(self.s.load_yaml("config/pricing.yaml").get("kling", {}).get("unit_usd", 0.14))
            fields["actual_cost_usd"] = round(st.billed_units * unit, 4)
            self.db.add_cost(amount_usd=fields["actual_cost_usd"], kind="actual", provider="kling",
                             episode=self.p.id, job_id=job["id"], note=f"{st.billed_units} units")
        self.db.update_job(job["id"], **fields)
        job = self.db.get_job(job["id"])  # type: ignore[assignment]
        if download and job["status"] == "succeeded":
            self._download(job)
        return job

    def _ensure_local(self, job: dict, scene_id: str) -> None:
        """Готовый результат с тем же ключом: копируем файл (в т.ч. из другого эпизода) или скачиваем."""
        dest = self.p.dir("scenes") / f"{scene_id}.mp4"
        if dest.exists():
            return
        if job.get("result_path"):
            src = self.s.projects_dir / job["episode"] / job["result_path"]
            if src.exists():
                dest.write_bytes(src.read_bytes())
                print(f"  {scene_id}: использован готовый результат задачи {job['id'][:8]} (без оплаты)")
                return
        if job.get("result_url"):
            video_provider(job["provider"], self.s).download(job["result_url"], dest)
            print(f"  {scene_id}: результат скачан повторно (без оплаты)")

    def _superseded(self, job: dict) -> bool:
        """Есть ли более новая готовая версия этой сцены (тогда старую задачу не скачиваем поверх)."""
        for j in self.db.jobs_for(self.p.id, job["scene_id"]):
            if (j["id"] != job["id"] and j["kind"] != "tts" and j["status"] in DONE_STATUSES
                    and j["created_at"] > job["created_at"]):
                return True
        return False

    def _download(self, job: dict) -> Path | None:
        if not job.get("result_url"):
            return None
        if self._superseded(job):
            print(f"  {job['scene_id']}: задача {job['id'][:8]} устарела — есть более новая версия сцены, не перезаписываю")
            return None
        dest = self._result_file(job)
        prov = video_provider(job["provider"], self.s)
        # Старый результат сцены сохраняем, а не затираем
        if dest.exists():
            dest.rename(dest.with_name(f"{dest.stem}.prev-{int(time.time())}.mp4"))
        prov.download(job["result_url"], dest)
        self.db.update_job(job["id"], result_path=str(dest.relative_to(self.p.path)))
        self.db.log("download", {"scene": job["scene_id"], "file": dest.name}, episode=self.p.id)
        return dest

    def _update_project_status(self, script: Script) -> None:
        missing = [sc.id for sc in script.scenes if sc.generator not in ("local",) and not self.p.scene_source(sc.id)]
        if not missing and self.p.status in ("approved", "voiced"):
            self.p.set_status("generated")
        elif missing:
            print(f"Ещё нет исходников сцен: {', '.join(missing)}")


def resolve_job(db: DB, job_prefix: str, status: str, note: str = "") -> dict:
    """Ручное разрешение «неясной» задачи после проверки в кабинете провайдера."""
    if status not in ("failed", "cancelled", "succeeded"):
        raise GenerationError("Статус: failed | cancelled | succeeded")
    with db.conn() as c:
        rows = c.execute("SELECT id FROM jobs WHERE id LIKE ?", (job_prefix + "%",)).fetchall()
    if len(rows) != 1:
        raise GenerationError(f"Найдено задач: {len(rows)} — уточните id")
    db.update_job(rows[0]["id"], status=status, error=note or f"resolved manually: {status}")
    return db.get_job(rows[0]["id"])  # type: ignore[return-value]
