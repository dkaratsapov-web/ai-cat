"""`studio bot` — Telegram-бот + мини-приложение студии. Работает на компьютере владельца.

  Telegram (long polling)  ← сообщения владельца, кнопки «Утверждаю/Отмена», уведомления
  Мини-приложение (HTTP)   ← вкладки Чат / Эпизод / База; открывается кнопкой «Студия» в чате с ботом
  Агенты: Claude-продюсер (Anthropic API, делает задачи) и Директор (OpenAI, смотрит работу и комментирует)

Мини-приложению Telegram нужен публичный HTTPS-адрес. Его даёт туннель Cloudflare (`cloudflared`, бесплатно,
без регистрации) — бот запускает его сам, если программа установлена; либо адрес задаётся в .env (MINIAPP_URL).
Каждый запрос мини-приложения проверяется подписью Telegram (initData) и ID владельца.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlparse

import yaml

from ..config import Settings, redact, secret
from ..db import DB
from ..project import list_projects, open_project
from .agent import Host, Producer, run_studio
from .telegram import Telegram, TelegramError

ROLE_NAMES = {"owner": "Вы", "system": "Система"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def check_init_data(init_data: str, bot_token: str, owner_id: int, max_age: int = 86400) -> bool:
    """Проверка подписи мини-приложения по документации Telegram (Validating data received via the Mini App)."""
    if not init_data:
        return False
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    got = pairs.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return False
    if time.time() - int(pairs.get("auth_date", "0") or 0) > max_age:
        return False
    try:
        return int(json.loads(pairs.get("user", "{}")).get("id", 0)) == owner_id
    except (ValueError, TypeError):
        return False


class BotApp(Host):
    def __init__(self, settings: Settings, *, port: int = 8765, dev: bool = False, tunnel: bool = True):
        self.s, self.port, self.dev, self.use_tunnel = settings, port, dev, tunnel
        token, owner = secret("TELEGRAM_BOT_TOKEN"), secret("TELEGRAM_OWNER_ID")
        if not dev and (not token or not owner):
            raise SystemExit("В .env нужны TELEGRAM_BOT_TOKEN и TELEGRAM_OWNER_ID (см. docs/BOT.md)")
        self.token, self.owner = token or "", int(owner or 0)
        self.tg = Telegram(self.token) if token else None
        self.cfg = yaml.safe_load((settings.root / "config" / "agents.yaml").read_text(encoding="utf-8"))
        self.dir = settings.root / "data" / "bot"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.feed_path = self.dir / "feed.jsonl"
        self.state_path = self.dir / "state.json"
        self.state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        self.db = DB(settings.db_path)
        self.pending: dict[str, dict] = {}
        self.busy = {"claude": False, "director": False, "paid": False}
        self.jobs: "queue.Queue[tuple]" = queue.Queue()
        self.lock = threading.Lock()
        self.url = secret("MINIAPP_URL") or ""
        self.producer: Producer | None = None
        if secret("ANTHROPIC_API_KEY"):
            self.producer = Producer(settings, self.cfg, self, self.dir / "claude_history.json")

    # ------------------------------------------------------------ лента и состояние
    def post(self, role: str, text: str, **extra) -> None:
        rec = {"id": uuid.uuid4().hex[:10], "role": role, "text": text, "at": now(), **extra}
        with self.lock, open(self.feed_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def feed(self, n: int = 200) -> list[dict]:
        if not self.feed_path.exists():
            return []
        lines = self.feed_path.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]

    def save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")

    @property
    def episode(self) -> str:
        ep = self.state.get("episode")
        if not ep:
            projs = list_projects(self.s)
            ep = projs[-1].id if projs else ""
        return ep

    def agent_label(self, key: str) -> str:
        a = self.cfg.get("producer" if key == "claude" else key, {})
        return f"{a.get('emoji', '')} {a.get('name', key)}".strip()

    def tell(self, role: str, text: str, buttons=None) -> None:
        """В ленту мини-приложения + сообщением в Telegram."""
        self.post(role, text)
        if self.tg:
            label = ROLE_NAMES.get(role) or self.agent_label(role)
            try:
                self.tg.send(self.owner, f"{label}:\n{text}", buttons)
            except TelegramError as e:
                print(f"Telegram: {e}")

    # ------------------------------------------------------------ Host для Claude
    def notify(self, text: str) -> None:
        """Ход работы агента: в ленту и коротко в Telegram — чтобы было видно, что задача идёт."""
        self.post("system", text)
        if self.tg:
            try:
                self.tg.send(self.owner, text)
            except TelegramError:
                pass

    def _director_budget(self) -> str | None:
        day = datetime.now().strftime("%Y-%m-%d")
        if self.state.get("dir_day") != day:
            self.state["dir_day"], self.state["dir_calls"] = day, 0
        lim = int(self.cfg["limits"].get("director_calls_per_day", 30))
        if self.state["dir_calls"] >= lim:
            return f"дневной лимит вопросов директору ({lim}) исчерпан"
        self.state["dir_calls"] += 1
        self.save_state()
        return None

    def _bridge(self, episode: str):
        from ..director.bridge import Bridge
        return Bridge(open_project(episode, self.s), self.db, mode="openai")

    def ask_director(self, episode: str, question: str, from_claude: bool = True, audit: bool = False) -> str:
        why = self._director_budget()
        if why:
            return f"ОТКАЗ: {why}"
        self.busy["director"] = True
        try:
            if from_claude:
                self.post("claude", f"→ {self.agent_label('director')}: {question}")
            ans = self._bridge(episode).ask(question, log_question=False, audit=audit)
            if not ans:
                return "директор недоступен (проверьте OPENAI_API_KEY)"
            self.tell("director", ans)
            return ans
        except Exception as e:   # noqa: BLE001
            return f"ОШИБКА директора: {redact(str(e))[:300]}"
        finally:
            self.busy["director"] = False

    def director_review(self, episode: str, kind: str, scene: str) -> str:
        from ..director import chat
        why = self._director_budget()
        if why:
            return f"ОТКАЗ: {why}"
        self.busy["director"] = True
        try:
            b = self._bridge(episode)
            data = (b.script_review() if kind == "script" else
                    b.scene_review(scene) if kind == "scene" else b.final_review(None))
            if not data:
                return "директор недоступен — пакет для ручного ревью сохранён в director_bridge/review_packages"
            text = chat._review_text(f"{kind}_review", scene, data)
            self.tell("director", text)
            return json.dumps(data, ensure_ascii=False)[:6000]
        except Exception as e:   # noqa: BLE001
            return f"ОШИБКА ревью: {redact(str(e))[:300]}"
        finally:
            self.busy["director"] = False

    def send_file(self, path: Path, caption: str) -> str:
        rel = path.resolve().relative_to(self.s.root.resolve()).as_posix()
        self.post("claude", caption or path.name, file=rel)
        if self.tg:
            try:
                self.tg.send_file(self.owner, path, caption)
            except TelegramError as e:
                return f"в мини-приложении показал, в Telegram не отправилось: {e}"
        return "отправлено владельцу"

    def request_paid(self, command: str, args: list[str], reason: str) -> str:
        if command not in ("generate", "voice") or "--yes" in args:
            return "ОТКАЗ: можно только generate/voice без --yes"
        code, est = run_studio(self.s.root, [command, *args], timeout=300)   # без --yes = только смета
        pid = uuid.uuid4().hex[:8]
        self.pending[pid] = {"id": pid, "command": command, "args": args, "reason": reason, "estimate": est[-2500:],
                             "status": "pending", "at": now()}
        self.tell("system", f"💳 Нужно ваше решение: studio {command} {' '.join(args)}\nЗачем: {reason}\n\n"
                            f"Смета:\n{est[-2500:]}",
                  buttons=[[("✅ Утверждаю", f"pay:{pid}:ok"), ("❌ Отмена", f"pay:{pid}:no")]])
        return (f"Смета отправлена владельцу (запрос {pid}). Платное не запущено — ждём кнопку «Утверждаю». "
                "Когда владелец решит, ты получишь системное сообщение.")

    def decide(self, pid: str, ok: bool) -> str:
        p = self.pending.get(pid)
        if not p or p["status"] != "pending":
            return "Запрос уже обработан или устарел"
        p["status"] = "approved" if ok else "rejected"
        if not ok:
            self.tell("system", f"❌ Отменено: studio {p['command']} {' '.join(p['args'])}")
            self.jobs.put(("claude", f"[Система] Владелец ОТКЛОНИЛ запрос {pid} (studio {p['command']}). Ничего не запущено."))
            return "Отменено"
        self.tell("system", f"✅ Утверждено, запускаю: studio {p['command']} {' '.join(p['args'])}")
        self.jobs.put(("paid", pid))
        return "Запускаю"

    def _run_paid(self, pid: str) -> None:
        p = self.pending[pid]
        self.busy["paid"] = True
        try:
            code, out = run_studio(self.s.root, [p["command"], *p["args"], "--yes"], timeout=3600)
            p["status"] = "done" if code == 0 else "failed"
            self.tell("system", f"{'✅ Готово' if code == 0 else '⚠️ Ошибка'}: studio {p['command']} "
                                f"{' '.join(p['args'])}\n{out[-1500:]}")
            self._claude_turn(f"[Система] Владелец УТВЕРДИЛ запрос {pid}; команда выполнена, код {code}. "
                              f"Вывод (хвост):\n{out[-3000:]}\nКоротко отчитайся владельцу и предложи следующий шаг.")
        finally:
            self.busy["paid"] = False

    # ------------------------------------------------------------ сообщения владельца
    def _claude_turn(self, text: str) -> None:
        if not self.producer:
            self.tell("system", "Claude не подключён: нет ANTHROPIC_API_KEY в .env")
            return
        self.busy["claude"] = True
        try:
            if self.tg:
                self.tg.typing(self.owner)
            ans = self.producer.chat(f"[текущий эпизод: {self.episode or 'не выбран'}]\n{text}")
            self.tell("claude", ans)
        except Exception as e:   # noqa: BLE001
            self.tell("system", f"Ошибка Claude: {type(e).__name__}: {redact(str(e))[:300]}")
        finally:
            self.busy["claude"] = False

    def handle_text(self, text: str, to: str = "claude", via: str = "tg") -> None:
        text = text.strip()
        if text in ("/start", "/app"):
            return self.send_app_button()
        if text == "/new":
            if self.producer:
                self.producer.reset()
            return self.tell("system", "Начали новый разговор с Claude (старый сохранён в сводке не будет).")
        if text.startswith("/ep"):
            ep = text[3:].strip()
            ids = [p.id for p in list_projects(self.s)]
            if ep in ids:
                self.state["episode"] = ep
                self.save_state()
                return self.tell("system", f"Текущий эпизод: {ep}")
            return self.tell("system", "Эпизоды:\n" + "\n".join(ids[-15:]) + "\nВыбор: /ep <id>")
        if text.startswith(("/d ", "/director ")):
            to, text = "director", text.split(" ", 1)[1]
        elif text.startswith(("/c ", "/claude ")):
            to, text = "claude", text.split(" ", 1)[1]
        elif via == "tg" and not text.startswith("/"):
            to = "team"           # по умолчанию: директор смотрит первым → аудит → Claude
        self.post("owner", text, to=to)
        ack = {"team": f"{self.agent_label('director')} взял задачу: смотрю работу и готовлю аудит для Claude…",
               "director": f"{self.agent_label('director')}: смотрю…",
               "claude": f"{self.agent_label('claude')}: взял в работу…",
               "both": f"{self.agent_label('director')} и {self.agent_label('claude')}: взяли в работу…"}.get(to)
        if ack:
            self.notify(ack + (f" (в очереди: {self.jobs.qsize()})" if self.jobs.qsize() else ""))
        self.jobs.put((to, text))

    def worker(self) -> None:
        while True:
            kind, payload = self.jobs.get()
            try:
                if kind == "paid":
                    self._run_paid(payload)
                elif kind == "team":
                    if not self.episode:
                        self.tell("system", "Сначала выберите эпизод: /ep")
                        continue
                    audit = self.ask_director(self.episode, payload, from_claude=False, audit=True)
                    if audit.startswith(("ОТКАЗ", "ОШИБКА", "директор недоступен")):
                        self.notify(f"Директор не смог: {audit[:200]}. Передаю задачу Claude без аудита.")
                        audit = "(аудита нет)"
                    else:
                        self.notify(f"{self.agent_label('claude')}: получил аудит директора, составляю план…")
                    self._claude_turn(f"Задача владельца: {payload}\n\nАудит директора (он уже посмотрел кадры):\n{audit}\n\n"
                                      "Сверь аудит с проектом. Если владелец не сказал явно «делай» — пришли пул правок "
                                      "по пунктам (что, где, бесплатно или платно, сколько стоит) и спроси утверждение; "
                                      "файлы до этого не меняй и платное не запрашивай.")
                elif kind in ("director", "both"):
                    if not self.episode:
                        self.tell("system", "Сначала выберите эпизод: /ep")
                        continue
                    ans = self.ask_director(self.episode, payload, from_claude=False)
                    if kind == "both" and not ans.startswith(("ОТКАЗ", "ОШИБКА", "директор недоступен")):
                        self._claude_turn(f"Владелец спросил обоих: {payload}\nДиректор ответил: {ans}\n"
                                          "Скажи, согласен ли, и сделай бесплатную часть.")
                    elif ans.startswith(("ОТКАЗ", "ОШИБКА", "директор")):
                        self.tell("system", ans)
                else:
                    self._claude_turn(payload)
            except Exception as e:   # noqa: BLE001
                self.tell("system", f"Ошибка: {redact(str(e))[:300]}")

    # ------------------------------------------------------------ Telegram
    def send_app_button(self) -> None:
        if not self.tg:
            return
        if self.url:
            self.tg.call("sendMessage", {"chat_id": self.owner, "text": "Студия «МяуРкетинг» — откройте мини-приложение:",
                                         "reply_markup": {"inline_keyboard": [[{"text": "🐱 Открыть студию",
                                                                                "web_app": {"url": self.url}}]]}})
        else:
            self.tg.send(self.owner, "Мини-приложение пока без адреса (нет туннеля). Пишите здесь — я передам агентам.")

    def poll(self) -> None:
        offset = None
        while True:
            try:
                ups = self.tg.updates(offset)
            except TelegramError as e:
                print(f"Telegram: {e}")
                time.sleep(5)
                continue
            for u in ups:
                offset = u["update_id"] + 1
                frm = (u.get("message") or u.get("callback_query") or {}).get("from", {})
                print(f"Telegram: сообщение от id {frm.get('id')}" + ("" if frm.get("id") == self.owner else
                      f" — НЕ владелец (в .env TELEGRAM_OWNER_ID={self.owner})"), flush=True)
                cb = u.get("callback_query")
                if cb:
                    if cb.get("from", {}).get("id") != self.owner:
                        continue
                    m = re.fullmatch(r"pay:(\w+):(ok|no)", cb.get("data", ""))
                    res = self.decide(m.group(1), m.group(2) == "ok") if m else "?"
                    self.tg.answer_callback(cb["id"], res)
                    msg = cb.get("message") or {}
                    if msg:
                        self.tg.drop_buttons(msg["chat"]["id"], msg["message_id"])
                    continue
                msg = u.get("message") or {}
                if msg.get("from", {}).get("id") != self.owner:
                    if msg.get("chat", {}).get("id"):
                        try:
                            self.tg.send(msg["chat"]["id"], "Это личный бот студии.")
                        except TelegramError:
                            pass
                    continue
                if msg.get("text"):
                    self.handle_text(msg["text"])

    # ------------------------------------------------------------ туннель
    def start_tunnel(self) -> None:
        exe = shutil.which("cloudflared") or next(   # после winget PATH обновится только в новом окне
            (str(c) for c in (Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
                              Path(r"C:\Program Files\cloudflared\cloudflared.exe")) if c.exists()), None)
        if self.url or not self.use_tunnel or not exe:
            if not self.url:
                print("Туннеля нет: мини-приложение откроется только после установки cloudflared (docs/BOT.md). "
                      "Чат с ботом в Telegram работает и без него.")
            return
        proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace")
        deadline = time.time() + 60
        for line in proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                self.url = m.group(0)
                break
            if time.time() > deadline:
                break
        threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()   # дренируем вывод
        if self.url and self.tg:
            try:
                self.tg.call("setChatMenuButton", {"chat_id": self.owner, "menu_button": {
                    "type": "web_app", "text": "Студия", "web_app": {"url": self.url}}})
            except TelegramError as e:
                print(f"Кнопка меню: {e}")
        print(f"Мини-приложение: {self.url or 'адрес не получен'}")

    # ------------------------------------------------------------ данные для мини-приложения
    def snapshot(self) -> dict:
        eps = [p.id for p in list_projects(self.s)]
        scenes = []
        ep = self.episode
        if ep:
            try:
                from ..director import state
                proj = open_project(ep, self.s)
                st = state.sync_from_jobs(proj, self.db)
                costs: dict[str, float] = {}
                for j in self.db.jobs_for(ep):
                    if j.get("paid") and j["status"] == "succeeded":
                        costs[j["scene_id"]] = costs.get(j["scene_id"], 0) + float(j.get("actual_cost_usd") or j.get("est_cost_usd") or 0)
                for sc in proj.load_script().scenes:
                    x = st.get(sc.id) or {}
                    scenes.append({"id": sc.id, "type": sc.type, "generator": sc.generator, "status": x.get("status", "—"),
                                   "v": x.get("versions", 0), "director": (x.get("director") or {}).get("status") or "—",
                                   "qc": (x.get("identity_qc") or {}).get("verdict") or "—",
                                   "cost": round(costs.get(sc.id, 0), 2), "text": (sc.subtitle_text or "")[:90]})
            except Exception as e:   # noqa: BLE001
                scenes = [{"id": "—", "text": f"не удалось прочитать эпизод: {e}"}]
        return {"feed": self.feed(), "busy": self.busy, "episode": ep, "episodes": eps, "scenes": scenes,
                "pending": [p for p in self.pending.values() if p["status"] == "pending"],
                "agents": {"claude": self.agent_label("claude"), "director": self.agent_label("director")},
                "claude_ready": bool(self.producer), "director_ready": bool(secret("OPENAI_API_KEY"))}

    # ------------------------------------------------------------ HTTP
    def serve(self) -> None:
        app = self
        page = (Path(__file__).parent / "miniapp.html").read_text(encoding="utf-8")
        allowed_roots = ("projects/", "assets/", "knowledge/")

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200) -> None:
                self._send(code, json.dumps(obj, ensure_ascii=False).encode())

            def _auth(self, q: dict) -> bool:
                if app.dev:
                    return True
                init = self.headers.get("X-Init-Data") or (q.get("auth") or [""])[0]
                return check_init_data(init, app.token, app.owner)

            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                if u.path in ("/", "/index.html"):
                    return self._send(200, page.encode(), "text/html; charset=utf-8")
                if not self._auth(q):
                    return self._json({"error": "нет доступа: откройте студию из Telegram"}, 403)
                if u.path == "/api/state":
                    return self._json(app.snapshot())
                if u.path == "/api/kb":
                    from ..knowledge import catalog, search
                    term = (q.get("q") or [""])[0].strip()
                    if term:
                        return self._json({"hits": search(app.s, term, 15)})
                    return self._json({"docs": [d.__dict__ for d in catalog(app.s)]})
                if u.path == "/api/file":
                    rel = (q.get("path") or [""])[0]
                    p = (app.s.root / rel).resolve()
                    if not rel.startswith(allowed_roots) or app.s.root.resolve() not in p.parents or not p.is_file() \
                            or p.name.startswith(".env"):
                        return self._json({"error": "файл недоступен"}, 404)
                    ctype = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                             ".webp": "image/webp"}.get(p.suffix.lower(), "text/plain; charset=utf-8")
                    return self._send(200, p.read_bytes(), ctype)
                return self._json({"error": "not found"}, 404)

            def do_POST(self):
                u = urlparse(self.path)
                if not self._auth(parse_qs(u.query)):
                    return self._json({"error": "нет доступа"}, 403)
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    data = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._json({"error": "bad json"}, 400)
                if u.path == "/api/send":
                    text, to = str(data.get("text", "")).strip(), data.get("to", "claude")
                    if not text or to not in ("team", "claude", "director", "both"):
                        return self._json({"error": "пустое сообщение"}, 400)
                    app.handle_text(text, to=to, via="app")
                    return self._json({"ok": True})
                if u.path == "/api/episode":
                    if data.get("id") in [p.id for p in list_projects(app.s)]:
                        app.state["episode"] = data["id"]
                        app.save_state()
                        return self._json({"ok": True})
                    return self._json({"error": "нет такого эпизода"}, 400)
                if u.path == "/api/pay":
                    return self._json({"result": app.decide(str(data.get("id")), bool(data.get("ok")))})
                return self._json({"error": "not found"}, 404)

        ThreadingHTTPServer(("127.0.0.1", self.port), H).serve_forever()

    def run(self) -> None:
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.serve, daemon=True).start()
        self.start_tunnel()
        print(f"Бот запущен. Claude: {'да' if self.producer else 'нет ключа'} · "
              f"Директор: {'да' if secret('OPENAI_API_KEY') else 'нет ключа'} · локально: http://127.0.0.1:{self.port}/"
              + (" (dev, без проверки Telegram)" if self.dev else ""))
        if self.tg:
            print("Подключаюсь к Telegram…", flush=True)
            try:
                me = self.tg.call("getMe", timeout=25)
                print(f"Telegram: @{me.get('username')}. Напишите боту /start. Остановить: Ctrl+C")
                self.send_app_button()
            except TelegramError as e:
                raise SystemExit(f"Нет связи с Telegram API или неверный токен: {e}\n"
                                 "Если это таймаут/ConnectionError — api.telegram.org недоступен из вашей сети.") from None
            self.poll()
        else:
            while True:
                time.sleep(3600)
