"""Локальная веб-панель (`studio web`): ролики, раскадровка, сметы и подтверждения в браузере.

Работает только на 127.0.0.1 (наружу не открывается). POST-запросы требуют токен, который выдаётся
странице при запуске, — другие сайты в браузере не могут нажимать кнопки за вас.
Платные операции выполняются только после явного подтверждения сметы в интерфейсе.
"""
from __future__ import annotations

import contextlib
import io
import json
import mimetypes
import re
import secrets
import threading
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from ..character.library import CharacterLibrary
from ..config import Settings, redact
from ..costs.budget import Budget
from ..db import DB
from ..models import CONTENT_TYPES, SCENE_TYPES, Script
from ..project import STATUSES, Project, create_project, list_projects, open_project
from .page import PAGE

STATUS_RU = {
    "draft": "Черновик", "approved": "Сценарий утверждён", "voiced": "Озвучен", "generated": "Сцены готовы",
    "assembled": "Смонтирован", "qa_failed": "Проверка не пройдена", "qa_passed": "Проверка пройдена",
    "final_approved": "Ролик утверждён", "packaged": "Готов к публикации", "cancelled": "Отменён",
}


class _Log(io.TextIOBase):
    """Перехват print() фоновой задачи в журнал, видимый в панели."""

    def __init__(self, task: dict):
        self.task = task

    def write(self, s: str) -> int:
        if s:
            self.task["log"] += redact(s)
            self.task["log"] = self.task["log"][-20000:]
        return len(s)


class Tasks:
    """Фоновые операции выполняются по одной (как в CLI) — без гонок за файлы и деньги."""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.busy = threading.Lock()

    def start(self, title: str, episode: str | None, fn: Callable[[], Any]) -> dict:
        if self.busy.locked():
            raise RuntimeError("Уже выполняется другая операция — дождитесь её окончания")
        task = {"id": uuid.uuid4().hex[:10], "title": title, "episode": episode, "status": "running", "log": "",
                "error": None}
        self.items[task["id"]] = task

        def run():
            with self.busy:
                buf = _Log(task)
                try:
                    with contextlib.redirect_stdout(buf):  # type: ignore[type-var]
                        fn()
                    task["status"] = "done"
                except Exception as e:  # noqa: BLE001 — показать любую ошибку в панели
                    task["status"] = "error"
                    task["error"] = redact(f"{type(e).__name__}: {e}")
                    task["log"] += redact(traceback.format_exc(limit=3))

        threading.Thread(target=run, daemon=True).start()
        return task

    def current(self) -> dict | None:
        running = [t for t in self.items.values() if t["status"] == "running"]
        return running[-1] if running else None


# Типы медиа задаём явно: на Windows mimetypes берёт их из реестра, где встречаются неверные значения.
MEDIA_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "video/mp4",
               ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _safe_file(base: Path, rel: str) -> Path | None:
    p = (base / unquote(rel)).resolve()
    if not p.is_relative_to(base.resolve()) or not p.is_file():
        return None
    return p


def episode_summary(p: Project) -> dict:
    m = p.meta()
    return {"id": p.id, "title": m.get("title", ""), "status": m.get("status", "draft"),
            "status_ru": STATUS_RU.get(m.get("status", "draft"), m.get("status")), "mock": p.id.endswith("--mock"),
            "updated_at": m.get("updated_at")}


def episode_detail(p: Project, s: Settings, db: DB) -> dict:
    from ..generation.runner import estimate, plan
    from ..generation.voice import estimate_voice, scene_voice_meta

    script = p.load_script()
    warnings: list[str] = []
    try:
        warnings = script.validate(s.get("video.min_duration"), s.get("video.max_duration"))
    except Exception as e:  # noqa: BLE001
        warnings = [f"Ошибка сценария: {e}"]
    pricing = s.load_yaml("config/pricing.yaml")
    lib = CharacterLibrary(s)
    refs = {r["id"]: r for r in lib.references()}
    approved = lib.references("approved")
    default_ref = approved[0] if approved else None  # без `reference` генерация берёт первый утверждённый

    video_est: dict[str, dict] = {}
    video_total = 0.0
    est_error = None
    try:
        jobs = plan(p, script, need_audio=False)
        est = estimate(p, jobs, db)
        for line in est.lines:
            video_est[line.scene_id] = {"usd": line.usd, "detail": f"{line.kind} · {line.detail}", "reused": line.reused}
        video_total = est.total
    except Exception as e:  # noqa: BLE001
        est_error = redact(str(e))
    voice_total = 0.0
    try:
        tts = s.get("providers.tts", "yandex")
        voice_total = estimate_voice(p, script, tts, pricing).total
    except Exception:  # noqa: BLE001
        pass

    db_jobs = db.jobs_for(p.id)
    scenes = []
    t = 0.0
    for sc in script.scenes:
        meta = scene_voice_meta(p, sc.id)
        src = p.scene_source(sc.id)
        ref = refs.get(sc.reference) if sc.reference else None
        if not sc.reference and sc.generator in ("kling", "hedra", "runway"):
            ref = default_ref
        img = None
        if sc.generator == "local" and sc.local.get("image"):
            img = sc.local["image"]
        last_job = next((j for j in reversed(db_jobs) if j["scene_id"] == sc.id and j["kind"] != "tts"), None)
        dur = meta["duration"] + 0.2 if meta else sc.duration
        scenes.append({
            "id": sc.id, "type": sc.type, "type_ru": SCENE_TYPES.get(sc.type, sc.type), "generator": sc.generator,
            "paid": sc.generator in ("kling", "hedra", "runway"), "start": round(t, 1), "duration": round(dur, 1),
            "voiceover": sc.voiceover, "visual": sc.visual, "animation": sc.animation,
            "reference": sc.reference or (f"{ref['id']} (по умолчанию)" if ref else None),
            "reference_file": ref["file"] if ref else None,
            "local_kind": sc.local.get("kind"), "local_image": img, "caption": sc.local.get("caption"),
            "title": sc.local.get("title"), "bullets": sc.local.get("bullets"),
            "has_audio": bool(meta), "audio_duration": meta["duration"] if meta else None,
            "has_clip": bool(src), "clip": src.name if src else None,
            "estimate": video_est.get(sc.id),
            "job": {k: last_job[k] for k in ("status", "error", "provider", "kind")} if last_job else None,
        })
        t += dur
    out = p.final_video
    qa_txt = p.dir("output") / "qa_report.txt"
    pub = p.dir("publish") / "publish.json"
    budget = Budget(s, db).status(p.id)
    m = p.meta()
    return {
        **episode_summary(p),
        "approved": p.is_script_approved(),
        "content_type": CONTENT_TYPES.get(script.content_type, script.content_type),
        "hook": script.hook, "cta": script.cta, "disclaimer": script.disclaimer, "audience": script.audience,
        "warnings": warnings, "scenes": scenes, "total_duration": round(t, 1),
        "estimate": {"video_usd": video_total, "voice_usd": voice_total, "error": est_error},
        "budget": budget,
        "final_video": out.name if out.exists() else None,
        "qa": qa_txt.read_text(encoding="utf-8") if qa_txt.exists() else None,
        "publish": json.loads(pub.read_text(encoding="utf-8")) if pub.exists() else None,
        "covers": sorted(x.name for x in p.dir("publish").glob("cover_*.jpg")),
        "history": m.get("history", [])[-8:],
        "statuses": list(STATUSES),
    }


class App:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = DB(settings.db_path)
        self.tasks = Tasks()
        self.token = secrets.token_urlsafe(24)

    # ------------------------------------------------------------ actions
    def action(self, episode: str, body: dict) -> dict:
        from ..editing.assemble import assemble
        from ..generation.runner import Runner
        from ..generation.voice import generate_voice
        from ..publishing.package import package
        from ..quality.checks import run_qa, save_report

        p = open_project(episode, self.s)
        act = body.get("action")
        confirm = bool(body.get("confirm"))
        scenes = body.get("scenes") or None
        regen = body.get("regenerate") or None

        if act == "approve_script":
            p.approve_script()
            self.db.log("approve_script", "web", episode=p.id)
            return {"ok": True}
        if act == "cancel":
            p.set_status("cancelled")
            return {"ok": True}
        if act == "edit_scene":
            script = p.load_script()
            sc = script.scene(body["scene"])
            if "voiceover" in body:
                sc.voiceover = str(body["voiceover"]).strip()
            if body.get("duration"):
                sc.duration = float(body["duration"])
            script.validate(self.s.get("video.min_duration"), self.s.get("video.max_duration"))
            p.save_script(script)
            if p.status not in ("draft", "cancelled"):
                p.set_status("draft")
            self.db.log("edit_scene", {"scene": sc.id, "via": "web"}, episode=p.id)
            return {"ok": True}
        if act == "approve_final":
            if p.status != "qa_passed":
                raise RuntimeError("Сначала должна пройти техническая проверка")
            p.set_status("final_approved")
            return {"ok": True}

        paid = act in ("voice", "generate")
        if paid and not confirm:
            raise RuntimeError("Платная операция требует подтверждения сметы")

        def run():
            if act == "voice":
                generate_voice(p, self.db, scenes=scenes, assume_yes=True, force=bool(body.get("force")))
            elif act == "generate":
                Runner(p, self.db).generate(scenes, assume_yes=True, regenerate=regen)
            elif act == "refresh":
                Runner(p, self.db).refresh()
            elif act == "assemble":
                out = assemble(p)
                print(f"Готово: {out.name}")
            elif act == "qa":
                rep = run_qa(p)
                save_report(p, rep)
                p.set_status("qa_passed" if rep.passed else "qa_failed")
                print(rep.text())
            elif act == "package":
                package(p)
                print("Пакет публикации готов")
            else:
                raise RuntimeError(f"Неизвестное действие: {act}")

        titles = {"voice": "Озвучка", "generate": "Генерация сцен", "refresh": "Проверка статуса задач",
                  "assemble": "Монтаж", "qa": "Техническая проверка", "package": "Пакет публикации"}
        task = self.tasks.start(titles.get(act, act), p.id, run)
        return {"ok": True, "task": task["id"]}

    def new_episode(self, template: str) -> dict:
        src = self.s.root / "assets/templates/scripts" / f"{template}.yaml"
        if not re.match(r"^[A-Za-z0-9_-]+$", template) or not src.exists():
            raise RuntimeError("Шаблон не найден")
        proj = create_project(Script.load(src), self.s)
        self.db.log("create", {"template": template, "via": "web"}, episode=proj.id)
        return {"ok": True, "id": proj.id}

    def templates(self) -> list[dict]:
        import yaml
        out = []
        for f in sorted((self.s.root / "assets/templates/scripts").glob("*.yaml")):
            d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            out.append({"id": f.stem, "title": d.get("title"), "type": CONTENT_TYPES.get(d.get("content_type"), "")})
        return out


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MeowStudio/1"

        def log_message(self, *args):  # тихо
            pass

        # ---------------------------------------------------- helpers
        def _json(self, data: Any, code: int = 200) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def _file(self, path: Path | None) -> None:
            if not path:
                self.send_error(404)
                return
            size = path.stat().st_size
            ctype = (MEDIA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
                     or "application/octet-stream")
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            if rng and (m := re.match(r"bytes=(\d*)-(\d*)$", rng.strip())) and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else end
                else:
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:  # диапазон вне файла (или пустой файл): иначе отрицательная Content-Length
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        return
                    left -= len(chunk)

        # ---------------------------------------------------- GET
        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    raw = PAGE.replace("__TOKEN__", app.token).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                elif path == "/api/episodes":
                    self._json([episode_summary(p) for p in list_projects(app.s)])
                elif path == "/api/templates":
                    self._json(app.templates())
                elif path == "/api/task":
                    self._json(app.tasks.current())
                elif m := re.match(r"^/api/task/([a-f0-9]+)$", path):
                    self._json(app.tasks.items.get(m.group(1)))
                elif m := re.match(r"^/api/episode/([A-Za-z0-9_-]+)$", path):
                    self._json(episode_detail(open_project(m.group(1), app.s), app.s, app.db))
                elif m := re.match(r"^/media/([A-Za-z0-9_-]+)/(.+)$", path):
                    self._file(_safe_file(app.s.projects_dir / m.group(1), m.group(2)))
                elif m := re.match(r"^/ref/(.+)$", path):
                    self._file(_safe_file(app.s.character_dir, m.group(1)))
                elif m := re.match(r"^/img/([A-Za-z0-9_-]+)/(.+)$", path):
                    from ..editing.local_scenes import LocalRenderError, resolve_image
                    try:
                        self._file(resolve_image(app.s.projects_dir / m.group(1), app.s, unquote(m.group(2))))
                    except LocalRenderError:
                        self.send_error(404)
                else:
                    self.send_error(404)
            except Exception as e:  # noqa: BLE001
                self._json({"error": redact(str(e))}, 400)

        # ---------------------------------------------------- POST
        def do_POST(self):  # noqa: N802
            if self.headers.get("X-Token") != app.token:
                self._json({"error": "нет доступа"}, 403)
                return
            path = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                if m := re.match(r"^/api/episode/([A-Za-z0-9_-]+)/action$", path):
                    self._json(app.action(m.group(1), body))
                elif path == "/api/new":
                    self._json(app.new_episode(str(body.get("template", ""))))
                else:
                    self.send_error(404)
            except Exception as e:  # noqa: BLE001
                self._json({"error": redact(str(e))}, 400)

    return Handler


def serve(settings: Settings, port: int = 8765, open_browser: bool = True) -> None:
    app = App(settings)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}/"
    print(f"Панель: {url}\nОстановить: Ctrl+C")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nПанель остановлена")
    finally:
        httpd.server_close()
