"""Минимальный клиент официального Telegram Bot API (https://core.telegram.org/bots/api) — long polling.

Токен живёт только в .env (TELEGRAM_BOT_TOKEN) и вырезается из любых текстов ошибок.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

MAX_TEXT = 4000          # лимит Telegram — 4096 символов на сообщение
MAX_UPLOAD = 49 * 1024 * 1024   # боты отправляют файлы до 50 МБ


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str):
        self._token = token
        self.base = f"https://api.telegram.org/bot{token}"

    def _safe(self, text: str) -> str:
        return str(text).replace(self._token, "<TOKEN>")

    def call(self, method: str, data: dict | None = None, files: dict | None = None, timeout: int = 70) -> dict | list:
        try:
            if files:
                r = requests.post(f"{self.base}/{method}", data=data, files=files, timeout=timeout)
            else:
                r = requests.post(f"{self.base}/{method}", json=data or {}, timeout=timeout)
        except requests.RequestException as e:
            raise TelegramError(self._safe(f"{type(e).__name__}: {e}")) from None
        try:
            j = r.json()
        except ValueError:
            raise TelegramError(f"Telegram HTTP {r.status_code}") from None
        if not j.get("ok"):
            raise TelegramError(self._safe(f"Telegram {method}: {j.get('error_code')} {j.get('description')}"))
        return j["result"]

    # ------------------------------------------------------------------ чтение
    def updates(self, offset: int | None) -> list[dict]:
        return self.call("getUpdates", {"offset": offset, "timeout": 50,
                                        "allowed_updates": ["message", "callback_query"]}, timeout=70)

    # ------------------------------------------------------------------ отправка
    def send(self, chat_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
        text = text or "—"
        parts = [text[i:i + MAX_TEXT] for i in range(0, len(text), MAX_TEXT)]
        for k, part in enumerate(parts):
            data: dict = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
            if buttons and k == len(parts) - 1:
                data["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row]
                                                            for row in buttons]}
            self.call("sendMessage", data)

    def typing(self, chat_id: int) -> None:
        try:
            self.call("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=15)
        except TelegramError:
            pass

    def answer_callback(self, cb_id: str, text: str = "") -> None:
        try:
            self.call("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:190]}, timeout=15)
        except TelegramError:
            pass

    def drop_buttons(self, chat_id: int, message_id: int) -> None:
        try:
            self.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                 "reply_markup": {"inline_keyboard": []}}, timeout=15)
        except TelegramError:
            pass

    def send_file(self, chat_id: int, path: Path, caption: str = "") -> None:
        if path.stat().st_size > MAX_UPLOAD:
            raise TelegramError(f"{path.name}: больше 50 МБ — Telegram не примет файл от бота")
        ext = path.suffix.lower()
        method, field = (("sendVideo", "video") if ext in (".mp4", ".mov") else
                         ("sendPhoto", "photo") if ext in (".jpg", ".jpeg", ".png", ".webp") else
                         ("sendDocument", "document"))
        with open(path, "rb") as f:
            self.call(method, {"chat_id": chat_id, "caption": caption[:1000]}, files={field: (path.name, f)},
                      timeout=300)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
