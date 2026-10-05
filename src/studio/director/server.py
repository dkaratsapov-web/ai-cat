"""Интерактивный чат эпизода: `studio chat <эпизод>` → http://127.0.0.1:8765

Владелец пишет в браузере. Адресаты:
  director — контент-директор (OpenAI Responses API, каждое сообщение — платный запрос на центы);
  claude   — локальный Claude Code в режиме `claude -p` (если установлен), работает в папке проекта;
  both     — сначала директор, потом Claude видит его ответ и действует/отвечает.

Ограничения Claude из чата: читать файлы, править сценарий, бесплатные команды studio. Платные команды
(generate, voice) запрещены на уровне инструментов — Claude только пишет владельцу, какую команду запустить.
Сервер слушает только 127.0.0.1.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..db import DB
from ..project import Project
from . import chat

CLAUDE_ALLOWED = ["Read", "Glob", "Grep", "Edit", "Write",
                  "Bash(studio script show:*)", "Bash(studio director status:*)",
                  "Bash(studio director sync:*)", "Bash(studio scene-status:*)", "Bash(studio costs:*)",
                  "Bash(studio jobs:*)", "Bash(studio assemble:*)", "Bash(studio qa:*)", "Bash(studio assets:*)",
                  "Bash(studio character list:*)", "Bash(studio pipeline:*)"]
CLAUDE_DENIED = ["Bash(studio generate:*)", "Bash(studio voice:*)", "Bash(studio director ask:*)",
                 "Bash(studio director review:*)", "Bash(studio director review-scene:*)",
                 "Bash(studio director review-final:*)", "Bash(studio approve:*)", "Bash(studio package:*)",
                 "Bash(studio script approve:*)", "Bash(studio idqc:*)", "Bash(studio character approve:*)",
                 "Read(./.env)", "Edit(./.env)", "Write(./.env)", "WebFetch", "WebSearch"]

CLAUDE_ROLE = """Ты — Claude, технический продюсер рубрики «МяуРкетинг» (см. CLAUDE.md в корне проекта — правила обязательны).
Ты в общем чате эпизода {ep} с владельцем и контент-директором (GPT). Отвечай по-русски, коротко.
Можно: читать файлы, править projects/{ep}/script/script.yaml и бесплатные команды studio (script show, assemble, qa, status).
Нельзя: платные команды (generate, voice), публикация, approve/package. Если нужно платное — покажи смету и напиши
владельцу точную команду для PowerShell; запускает он сам после «утверждаю».
Последние сообщения чата:
{recent}

Сообщение для тебя:
{text}"""


class ChatServer:
    def __init__(self, project: Project, db: DB, mode: str = "openai"):
        self.p, self.db, self.mode = project, db, mode
        self.busy: dict[str, bool] = {"director": False, "claude": False}
        self.lock = threading.Lock()
        self.claude_bin = shutil.which("claude")

    # ------------------------------------------------------------ участники
    def _director(self, text: str) -> str | None:
        from .bridge import Bridge
        self.busy["director"] = True
        try:
            return Bridge(self.p, self.db, mode=self.mode).ask(text, log_question=False)
        except Exception as e:   # noqa: BLE001 — ошибку показываем в чате, сервер не падает
            chat.append(self.p, "claude", f"Директор не ответил: {type(e).__name__}: {str(e)[:300]}")
            return None
        finally:
            self.busy["director"] = False

    def _claude(self, text: str) -> None:
        if not self.claude_bin:
            chat.append(self.p, "claude", "Claude Code на этом компьютере не найден (команда `claude`). Установите его — "
                                          "тогда я смогу отвечать и работать прямо в этом чате. Пока пишите мне в приложении Claude.")
            return
        self.busy["claude"] = True
        try:
            sess_file = self.p.path / "director" / "claude_session.txt"
            prompt = CLAUDE_ROLE.format(ep=self.p.id, recent=chat.recent_text(self.p, 12), text=text)
            cmd = [self.claude_bin, "-p", "Задание и контекст — во входных данных (stdin).", "--output-format", "json",
                   "--allowedTools", *CLAUDE_ALLOWED, "--disallowedTools", *CLAUDE_DENIED]
            if sess_file.exists():
                cmd += ["--resume", sess_file.read_text(encoding="utf-8").strip()]
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                               cwd=self.p.settings.root, timeout=900)
            try:
                out = json.loads(r.stdout)
                if out.get("session_id"):
                    sess_file.write_text(out["session_id"], encoding="utf-8")
                answer = out.get("result") or "(пустой ответ)"
            except ValueError:
                answer = (r.stdout or r.stderr or f"код выхода {r.returncode}").strip()[:3000]
            chat.append(self.p, "claude", answer)
        except subprocess.TimeoutExpired:
            chat.append(self.p, "claude", "Не уложился в 15 минут — задача остановлена. Разбейте её на части.")
        finally:
            self.busy["claude"] = False

    def handle(self, to: str, text: str) -> None:
        chat.append(self.p, "owner", text, to=to)
        if to in ("director", "both"):
            ans = self._director(text)
            if to == "both" and ans:
                text = f"Владелец: {text}\n\nОтвет директора: {ans}\n\nОцени, согласен ли, и сделай бесплатную часть."
        if to in ("claude", "both"):
            self._claude(text)

    # ------------------------------------------------------------ http
    def feed(self) -> dict:
        msgs = chat.collect(self.p, self.db)
        return {"messages": [{"role": m.get("role"), "text": m.get("text", ""), "at": m.get("at"),
                              "scene": m.get("scene") or ""} for m in msgs],
                "busy": self.busy, "claude": bool(self.claude_bin), "mode": self.mode}

    def serve(self, port: int = 8765, open_browser: bool = True) -> None:
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # не засоряем консоль
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/api/feed"):
                    self._send(200, json.dumps(srv.feed(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
                elif self.path in ("/", "/index.html"):
                    self._send(200, PAGE.replace("__EP__", srv.p.id).encode(), "text/html; charset=utf-8")
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self):
                # свой заголовок: чужая страница в браузере не сможет отправить сообщение от вашего имени
                if self.path != "/api/send" or self.headers.get("X-Studio") != "1":
                    return self._send(403, b"forbidden", "text/plain")
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}")
                text, to = str(data.get("text", "")).strip(), data.get("to", "director")
                if not text or to not in ("director", "claude", "both"):
                    return self._send(400, b"bad request", "text/plain")
                if srv.busy["director"] or srv.busy["claude"]:
                    return self._send(409, "Подождите ответа".encode(), "text/plain; charset=utf-8")
                threading.Thread(target=srv.handle, args=(to, text), daemon=True).start()
                self._send(202, b"{}", "application/json")

        httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        url = f"http://127.0.0.1:{port}/"
        print(f"Чат эпизода {self.p.id}: {url}\n  Директор: {'заглушка (mock)' if self.mode == 'mock' else 'OpenAI'} · "
              f"Claude: {'Claude Code найден' if self.claude_bin else 'Claude Code не установлен'}\n  Остановить: Ctrl+C")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nЧат остановлен.")


PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Чат эпизода</title><style>""" + chat.CSS + """
html,body{height:100%}body{display:flex;flex-direction:column}main{flex:1;overflow:auto;width:100%}
footer{background:var(--card);border-top:1px solid var(--line);padding:10px 16px}
.row{max-width:820px;margin:0 auto;display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end}
textarea{flex:1 1 300px;min-height:44px;max-height:180px;padding:9px 10px;border:1px solid var(--line);border-radius:10px;
background:var(--bg);color:var(--fg);font:inherit;resize:vertical}
.to{font-size:13px}.to label{font-size:13px;margin-right:10px;cursor:pointer;white-space:nowrap}.to input{width:auto;margin:0 4px 0 0;padding:0;vertical-align:middle}button{padding:10px 16px;border:0;border-radius:10px;
background:#2563eb;color:#fff;font:inherit;cursor:pointer}button:disabled{opacity:.5;cursor:default}
#st{font-size:12px;color:var(--muted);max-width:820px;margin:4px auto 0}
</style></head><body>
<header><h1>__EP__</h1><div class="legend"><span><i style="background:var(--owner)"></i>Владелец</span>
<span><i style="background:var(--director)"></i>Директор · GPT</span><span><i style="background:var(--claude)"></i>Claude · продюсер</span></div>
<input id="f" placeholder="Фильтр по сцене, например s04"></header>
<main id="feed"></main>
<footer><div class="row"><div class="to">Кому:
<label><input type="radio" name="to" value="director" checked> Директору</label>
<label><input type="radio" name="to" value="claude"> Claude</label>
<label><input type="radio" name="to" value="both"> Обоим</label></div></div>
<div class="row"><textarea id="t" placeholder="Сообщение… (Ctrl+Enter — отправить)"></textarea><button id="b">Отправить</button></div>
<div id="st"></div></footer>
<script>
const feed=document.getElementById('feed'),t=document.getElementById('t'),b=document.getElementById('b'),st=document.getElementById('st'),f=document.getElementById('f');
const NAMES={owner:'Владелец',director:'Директор · GPT',claude:'Claude · продюсер'};let last='';
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function load(){try{const r=await fetch('/api/feed');const d=await r.json();
 const key=JSON.stringify(d.messages.length)+JSON.stringify(d.busy)+f.value;
 const busy=d.busy.director||d.busy.claude;b.disabled=busy;
 st.textContent=(d.busy.director?'Директор думает… ':'')+(d.busy.claude?'Claude работает… ':'')+
  (busy?'':'Директор: '+(d.mode==='mock'?'заглушка':'OpenAI, каждое сообщение — запрос на центы')+' · Claude: '+(d.claude?'подключён':'не установлен на компьютере'));
 if(key===last)return;last=key;const atBottom=feed.scrollHeight-feed.scrollTop-feed.clientHeight<80;
 feed.innerHTML=d.messages.filter(m=>!f.value.trim()||m.scene===f.value.trim()).map(m=>{const dt=m.at?new Date(m.at.length===15?m.at.replace(/(\\d{4})(\\d{2})(\\d{2})-(\\d{2})(\\d{2})(\\d{2})/,'$1-$2-$3T$4:$5:$6Z'):m.at):null;
  return `<div class="msg ${m.role in NAMES?m.role:'claude'}"><div class="meta">${NAMES[m.role]||m.role}${dt&&!isNaN(dt)?' · '+dt.toLocaleString('ru',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):''}</div>${esc(m.text||'')}</div>`}).join('')||'<p class="tools">Пока пусто.</p>';
 if(atBottom||!feed.dataset.init){feed.scrollTop=feed.scrollHeight;feed.dataset.init=1}}catch(e){st.textContent='Нет связи с сервером чата (окно PowerShell закрыто?)'}}
async function send(){const text=t.value.trim();if(!text)return;const to=document.querySelector('input[name=to]:checked').value;
 b.disabled=true;const r=await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json','X-Studio':'1'},body:JSON.stringify({to,text})});
 if(r.ok){t.value=''}else{st.textContent=await r.text()}load()}
b.onclick=send;t.onkeydown=e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey))send()};f.oninput=()=>{last='';load()};
load();setInterval(load,2500);
</script></body></html>"""
