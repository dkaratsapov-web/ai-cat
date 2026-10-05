"""Claude-продюсер для Telegram-бота: Claude API (официальный SDK `anthropic`) + инструменты над проектом.

Что Claude может сам: читать файлы проекта, править сценарий и заметки директора, запускать бесплатные
команды studio, спрашивать директора (GPT), присылать файлы владельцу.
Платное (generate, voice) — только запрос: бот показывает владельцу смету с кнопками, запускает после «Утверждаю».

История — только дописывается (append-only): так её принимает API с сохранённым размышлением модели
(preserved thinking). Когда переписка длинная — сжимается в сводку и начинается заново.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from ..config import Settings, redact

# ------------------------------------------------------------------ правила команд
FREE_COMMANDS = {            # первое слово (или пара слов) команды studio → можно запускать без владельца
    ("script", "show"), ("status",), ("scene-status",), ("costs",), ("assemble",), ("qa",), ("history",),
    ("director", "status"), ("director", "sync"), ("director", "chat"), ("assets",), ("character", "list"),
    ("templates",), ("pipeline",), ("brief", "check"), ("review-pack",), ("director", "export-review-package"),
}
PAID_COMMANDS = {"generate", "voice"}
READONLY_COMMANDS = {("script", "show"), ("status",), ("scene-status",), ("costs",), ("history",),
                     ("director", "status"), ("assets",), ("character", "list"), ("templates",)}
ADVISOR_TOOLS = {"studio", "read_file", "kb_search", "kb_catalog", "view_image"}
WRITABLE = ("script/script.yaml", "director/director_notes.md")
SECRET_NAMES = (".env",)

TOOLS = [
    {"name": "studio", "strict": True,
     "description": "Запустить бесплатную команду studio в папке проекта и получить её вывод. Разрешены: "
                    "script show, status, scene-status, costs, assemble, qa, history, director status|sync, assets, "
                    "character list, templates, pipeline (только с --mock), brief check, review-pack. "
                    "Платные generate/voice — только через request_paid. Пример args: [\"qa\", \"episode-fitness-top5\"]",
     "input_schema": {"type": "object", "properties": {"args": {"type": "array", "items": {"type": "string"}}},
                      "required": ["args"], "additionalProperties": False}},
    {"name": "read_file", "strict": True,
     "description": "Прочитать текстовый файл проекта (путь от корня, например projects/<эпизод>/script/script.yaml). "
                    "Файлы .env недоступны.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                      "required": ["path"], "additionalProperties": False}},
    {"name": "write_file", "strict": True,
     "description": "Перезаписать файл целиком. Разрешено только projects/<эпизод>/script/script.yaml и "
                    "projects/<эпизод>/director/director_notes.md. Перед правкой прочитай файл.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                      "required": ["path", "content"], "additionalProperties": False}},
    {"name": "ask_director", "strict": True,
     "description": "Задать вопрос контент-директору (GPT) по эпизоду. Ответ увидит и владелец. Каждый вопрос — "
                    "платный запрос на центы, есть дневной лимит: спрашивай по делу.",
     "input_schema": {"type": "object", "properties": {"episode": {"type": "string"}, "question": {"type": "string"}},
                      "required": ["episode", "question"], "additionalProperties": False}},
    {"name": "director_review", "strict": True,
     "description": "Отдать работу директору (GPT) на ревью — он видит кадры, а не только текст. kind: script "
                    "(раскадровка по кадрам сцен), scene (кадры сгенерированного клипа + референс кота; нужен scene), "
                    "final (контактный лист ролика с таймкодами, субтитры, карта сцен; нужен собранный ролик). "
                    "Ответ сохраняется в проекте и приходит владельцу. Платно: центы за ревью.",
     "input_schema": {"type": "object", "properties": {
         "episode": {"type": "string"}, "kind": {"type": "string", "enum": ["script", "scene", "final"]},
         "scene": {"type": "string"}},
         "required": ["episode", "kind", "scene"], "additionalProperties": False}},
    {"name": "request_paid", "strict": True,
     "description": "Попросить владельца утвердить платную команду: generate (видео) или voice (озвучка). Бот "
                    "покажет смету и кнопки «Утверждаю/Отмена»; запустит только после нажатия. args — аргументы "
                    "после имени команды, например [\"episode-fitness-top5\", \"--scenes\", \"s04\", \"--regenerate\", \"s04\"].",
     "input_schema": {"type": "object", "properties": {
         "command": {"type": "string", "enum": ["generate", "voice"]},
         "args": {"type": "array", "items": {"type": "string"}},
         "reason": {"type": "string"}},
         "required": ["command", "args", "reason"], "additionalProperties": False}},
    {"name": "kb_search", "strict": True,
     "description": "Поиск по базе знаний и материалов студии: кейсы и заметки владельца (knowledge/), паспорт бренда, "
                    "правила, контент-план, разборы, брифы, сценарии и решения по эпизодам, ассеты. Возвращает пути и "
                    "фрагменты; полный текст — через read_file, картинку — через view_image.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                      "required": ["query"], "additionalProperties": False}},
    {"name": "kb_catalog", "strict": True,
     "description": "Оглавление всей базы знаний и материалов (что вообще есть).",
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "view_image", "strict": True,
     "description": "Посмотреть картинку проекта: кадр кота, контактный лист ролика, скриншот, логотип "
                    "(jpg/png/webp). Внешность кота окончательно оценивает владелец — ты даёшь наблюдения.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                      "required": ["path"], "additionalProperties": False}},
    {"name": "send_file", "strict": True,
     "description": "Прислать владельцу в Telegram файл проекта: видео (mp4), кадр/контактный лист (jpg/png) или отчёт.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "caption": {"type": "string"}},
                      "required": ["path", "caption"], "additionalProperties": False}},
]


class Host:
    """То, что агенту даёт бот: сообщения владельцу, кнопки, вызов директора."""
    request_paid: Callable[[str, list[str], str], str]
    send_file: Callable[[Path, str], str]
    ask_director: Callable[[str, str], str]
    director_review: Callable[[str, str, str], str]
    notify: Callable[[str], None]


CANCEL = __import__("threading").Event()   # «стоп» владельца: прерывает команды и шаги агентов


class Cancelled(RuntimeError):
    pass


def run_studio(root: Path, args: list[str], timeout: int = 1800) -> tuple[int, str]:
    import tempfile
    import time
    env = dict(os.environ, PYTHONIOENCODING="utf-8", STUDIO_ROOT=str(root))
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as out:
        proc = subprocess.Popen([sys.executable, "-m", "studio.cli", *args], cwd=root, stdout=out,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env,
                                stdin=subprocess.DEVNULL)
        start = time.time()
        while proc.poll() is None:
            if CANCEL.is_set() or time.time() - start > timeout:
                proc.kill()
                proc.wait()
                return 130, "Остановлено владельцем" if CANCEL.is_set() else f"Команда не уложилась в {timeout // 60} мин"
            time.sleep(0.3)
        out.seek(0)
        return proc.returncode, redact(out.read().strip())


def _inside(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if root.resolve() not in p.parents and p != root.resolve():
        raise ValueError("путь вне проекта")
    if p.name.startswith(SECRET_NAMES) or "data" in p.relative_to(root.resolve()).parts[:1]:
        raise ValueError("этот файл недоступен")
    return p


def check_free(args: list[str]) -> str | None:
    """None — можно запускать; иначе причина отказа."""
    if not args:
        return "пустая команда"
    if args[0] in PAID_COMMANDS:
        return f"{args[0]} — платная команда: используй request_paid"
    if not any(tuple(args[:len(k)]) == k for k in FREE_COMMANDS):
        return f"команда «{' '.join(args[:2])}» не входит в список разрешённых"
    if args[0] == "pipeline" and "--mock" not in args:
        return "pipeline разрешён только с --mock (иначе это платная генерация)"
    if "--yes" in args:
        return "--yes ставит только владелец"
    return None


def skills_note(root: Path, names: list[str]) -> str:
    """Список методик агента: перед задачей по теме агент читает нужную (read_file) — так знания не раздувают
    каждый запрос."""
    rows = []
    for n in names:
        f = root / "knowledge" / "skills" / f"{n}.md"
        if f.exists():
            desc = ""
            for line in f.read_text(encoding="utf-8").splitlines()[:12]:
                if line.lower().startswith("description:"):
                    desc = line.split(":", 1)[1].strip().strip('"')[:160]
            rows.append(f"- knowledge/skills/{n}.md — {desc}")
    if not rows:
        return ""
    return ("ТВОИ МЕТОДИКИ (прочитай нужную через read_file перед работой по теме; приёмы бери, «статистику» без "
            "источника в ролики не переноси):\n" + "\n".join(rows))


class Producer:
    def __init__(self, settings: Settings, cfg: dict, host: Host, store: Path, *, advisor: bool = False):
        """advisor=True — режим «поговорить и посоветовать»: только чтение (база знаний, файлы, статусы),
        ничего не меняет и не запускает сборку. Отдельная история — работает параллельно с задачами."""
        import anthropic   # pip install -e ".[bot]"
        self.s, self.cfg, self.host, self.advisor = settings, cfg, host, advisor
        self.tools = [t for t in TOOLS if t["name"] in ADVISOR_TOOLS] if advisor else TOOLS
        self.root = settings.root
        self.client = anthropic.Anthropic()   # ключ — ANTHROPIC_API_KEY из окружения/.env
        self.store = store
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load()

    # ------------------------------------------------------------ история
    def _system(self) -> str:
        p = self.cfg["producer"]
        rules = (self.root / "CLAUDE.md").read_text(encoding="utf-8") if (self.root / "CLAUDE.md").exists() else ""
        return (f"{p.get('persona', '').strip()}\n\nТы работаешь через Telegram-бота владельца. Сегодня "
                f"{date.today():%d.%m.%Y} (сессия начата). Инструменты работают на компьютере владельца в папке проекта.\n"
                "Правила денег: платное (generate, voice) — только через request_paid, никогда не обещай, что запустил "
                "платное сам. Не выдумывай факты и цены. Внешность кота и губы проверяет только владелец глазами.\n"
                "Директор (GPT) — советник: его мнение учитывай, решения принимает владелец.\n"
                "Ответы — простой текст без Markdown-таблиц (это Telegram), коротко.\n"
                + ("РЕЖИМ СОВЕТНИКА: владелец просто общается или спрашивает совета. Отвечай живо и по делу, опирайся "
                   "на базу знаний и данные проекта. Ничего не меняй и не запускай; если нужна работа — предложи "
                   "поставить задачу команде (написать «сделай …»). Про ход текущих задач отвечай по строке "
                   "[сейчас в работе: …] в начале сообщения.\n" if self.advisor else "") + "\n"
                f"{skills_note(self.root, p.get('skills') or [])}\n\n"
                f"=== Инструкции проекта (CLAUDE.md) ===\n{rules}")

    def _load(self) -> dict:
        if self.store.exists():
            try:
                return json.loads(self.store.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return self._fresh()

    def _fresh(self, summary: str = "") -> dict:
        msgs = []
        if summary:
            msgs.append({"role": "user", "content": f"[Сводка прошлого разговора — для контекста]\n{summary}"})
        return {"system": self._system(), "messages": msgs, "day": str(date.today()), "turns": 0}

    def _save(self) -> None:
        tmp = self.store.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.store)

    def reset(self) -> None:
        self.state = self._fresh()
        self._save()

    def _compact_if_needed(self) -> None:
        if len(self.state["messages"]) < int(self.cfg["limits"].get("history_messages", 80)):
            return
        lines = []
        for m in self.state["messages"]:
            c = m["content"]
            if isinstance(c, str):
                lines.append(f"{m['role']}: {c}")
            else:
                lines += [f"{m['role']}: {b['text']}" for b in c if b.get("type") == "text"]
        transcript = "\n".join(lines)[-60000:]
        r = self.client.messages.create(
            model=self.cfg["producer"]["model"], max_tokens=4000,
            messages=[{"role": "user", "content": "Сожми переписку владельца и продюсера в сводку (до 300 слов): "
                                                  "текущие эпизоды и их состояние, принятые решения, утверждённые и "
                                                  "отклонённые траты, открытые задачи.\n\n" + transcript}])
        summary = "".join(b.text for b in r.content if b.type == "text")
        self.state = self._fresh(summary)
        self._save()

    # ------------------------------------------------------------ инструменты
    def _tool(self, name: str, inp: dict):
        try:
            if name == "studio":
                args = [str(a) for a in inp["args"]]
                why = check_free(args)
                if not why and self.advisor and not any(tuple(args[:len(k)]) == k for k in READONLY_COMMANDS):
                    why = "в режиме совета — только просмотр (status, script show, costs…); задачу поставьте команде"
                if why:
                    return f"ОТКАЗ: {why}"
                self.host.notify(f"⚙️ studio {' '.join(args)}")
                code, out = run_studio(self.root, args)
                return f"код выхода {code}\n{out[-8000:]}"
            if name == "read_file":
                p = _inside(self.root, inp["path"])
                if not p.is_file():
                    return "файл не найден"
                return p.read_text(encoding="utf-8", errors="replace")[:30000]
            if name == "write_file":
                p = _inside(self.root, inp["path"])
                rel = p.relative_to(self.root.resolve()).as_posix()
                if not (rel.startswith("projects/") and rel.endswith(WRITABLE)):
                    return "ОТКАЗ: писать можно только script.yaml и director_notes.md эпизода"
                if p.exists():
                    bak = p.with_name(p.name + f".bak-{datetime.now():%Y%m%d-%H%M%S}")
                    bak.write_bytes(p.read_bytes())
                p.write_text(inp["content"], encoding="utf-8")
                self.host.notify(f"✏️ изменён {rel} (копия старой версии сохранена рядом)")
                return "сохранено"
            if name == "ask_director":
                return self.host.ask_director(inp["episode"], inp["question"])
            if name == "director_review":
                return self.host.director_review(inp["episode"], inp["kind"], inp.get("scene") or "")
            if name == "request_paid":
                return self.host.request_paid(inp["command"], [str(a) for a in inp["args"]], inp["reason"])
            if name == "kb_search":
                from ..knowledge import search
                hits = search(self.s, inp["query"])
                return "\n\n".join(f"[{h['section']}] {h['path']}\n{h['snippet']}" for h in hits) or "ничего не найдено"
            if name == "kb_catalog":
                from ..knowledge import catalog_text
                return catalog_text(self.s, 12000)
            if name == "view_image":
                return self._image(inp["path"])
            if name == "send_file":
                p = _inside(self.root, inp["path"])
                if not p.is_file():
                    return "файл не найден"
                return self.host.send_file(p, inp.get("caption", ""))
        except Exception as e:   # noqa: BLE001 — ошибка инструмента возвращается модели, бот не падает
            return f"ОШИБКА: {type(e).__name__}: {redact(str(e))[:500]}"
        return f"неизвестный инструмент {name}"

    def _image(self, rel: str):
        import base64
        import io

        from PIL import Image
        p = _inside(self.root, rel)
        if not p.is_file() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            return "картинка не найдена (jpg/png/webp)"
        im = Image.open(p).convert("RGB")
        im.thumbnail((1568, 1568))   # крупнее модель всё равно уменьшит — экономим токены
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(buf.getvalue()).decode()}},
                {"type": "text", "text": f"{rel} ({im.width}×{im.height})"}]

    # ------------------------------------------------------------ диалог
    def chat(self, text: str) -> str:
        import anthropic
        if self.state.get("day") != str(date.today()):
            self.state["day"], self.state["turns"] = str(date.today()), 0
        lim = int(self.cfg["limits"].get("claude_turns_per_day", 200))
        if self.state["turns"] >= lim:
            return f"Дневной лимит сообщений Claude ({lim}) исчерпан — его можно поднять в config/agents.yaml."
        self._compact_if_needed()
        self.state["turns"] += 1
        msgs = self.state["messages"]
        start = len(msgs)
        msgs.append({"role": "user", "content": text})
        p = self.cfg["producer"]
        kwargs: dict = {"model": p["model"], "max_tokens": int(p.get("max_tokens", 16000)),
                        "system": self.state["system"], "tools": self.tools, "messages": msgs,
                        "cache_control": {"type": "ephemeral"}}
        if p.get("effort"):
            kwargs["output_config"] = {"effort": p["effort"]}
        if p.get("fallback", True):
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        final = ""
        for _step in range(25):   # не больше 25 шагов с инструментами на одно сообщение
            if CANCEL.is_set():
                final = "Остановлено по вашей команде «стоп»."
                break
            try:
                resp = self.client.beta.messages.create(**kwargs)
            except anthropic.AuthenticationError:
                del msgs[start:]
                return "Anthropic не принял ключ: проверьте ANTHROPIC_API_KEY в .env."
            except anthropic.PermissionDeniedError as e:
                del msgs[start:]
                return f"Anthropic: нет доступа ({redact(str(e))[:200]}). Проверьте баланс и права ключа."
            except anthropic.RateLimitError:
                del msgs[start:]
                return "Anthropic: слишком много запросов — попробуйте через минуту."
            except anthropic.APIStatusError as e:
                del msgs[start:]
                return f"Anthropic HTTP {e.status_code}: {redact(str(e.message))[:300]}"
            except anthropic.APIConnectionError:
                del msgs[start:]
                return "Нет связи с Anthropic API. Проверьте интернет."
            blocks = [b.model_dump(exclude_none=True) for b in resp.content]
            msgs.append({"role": "assistant", "content": blocks})
            final = "".join(b.text for b in resp.content if b.type == "text").strip() or final
            if resp.stop_reason == "refusal":
                final = "Модель отказалась отвечать на это сообщение (правила безопасности). Переформулируйте."
                break
            if resp.stop_reason != "tool_use":
                break
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    out = self._tool(b.name, b.input if isinstance(b.input, dict) else {})
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
            msgs.append({"role": "user", "content": results})
            self._save()
        else:
            final += "\n(остановился: слишком много шагов подряд — напишите, продолжать ли)"
        self._save()
        return final or "(без текста)"
