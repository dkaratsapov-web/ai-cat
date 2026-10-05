"""Адаптер OpenAI Responses API (контент-директор). Без SDK: requests + контракт, сверенный по официальному
SDK openai 3.24.0 (PyPI, OpenAI):

  POST https://api.openai.com/v1/responses        Authorization: Bearer <OPENAI_API_KEY>
  {model, instructions, input: [{role: "user", content: [{type: "input_text", text},
                                                         {type: "input_image", image_url: "data:image/jpeg;base64,...", detail}]}],
   previous_response_id, store, max_output_tokens,
   text: {format: {type: "json_schema", name, schema, strict: true}}}
  → {id, status, output: [{type: "message", content: [{type: "output_text", text}]}], usage: {...}}

Видео как вход API не принимает — отправляем кадры и контактные листы.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import requests

from ..config import redact, secret


class DirectorUnavailable(RuntimeError):
    """Нет ключа / сети / ответа — включаем ручной режим (manual_review), проект не блокируется."""


def _image_part(path: Path, detail: str) -> dict:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode()
    return {"type": "input_image", "image_url": f"data:{mime};base64,{data}", "detail": detail}


def build_request(cfg: dict, *, instructions: str, text: str, images: list[Path], schema_name: str, schema: dict,
                  previous_response_id: str | None) -> dict:
    content: list[dict] = [{"type": "input_text", "text": text}]
    content += [_image_part(p, cfg.get("image_detail", "auto")) for p in images[: int(cfg.get("max_images", 12))]]
    body: dict[str, Any] = {
        "model": cfg["model"], "instructions": instructions,
        "input": [{"role": "user", "content": content}],
        "store": bool(cfg.get("store", True)),
        "max_output_tokens": int(cfg.get("max_output_tokens", 4000)),
        "text": {"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
    }
    if previous_response_id:
        body["previous_response_id"] = previous_response_id
    return body


def output_text(resp: dict) -> str:
    parts = []
    for item in resp.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") == "output_text" and c.get("text") is not None:
                    parts.append(c["text"])
    return "".join(parts)


class OpenAIDirector:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def configured(self) -> tuple[bool, str]:
        return (True, "OPENAI_API_KEY") if secret("OPENAI_API_KEY") else (False, "нет OPENAI_API_KEY в .env")

    def send(self, body: dict) -> dict:
        key = secret("OPENAI_API_KEY")
        if not key:
            raise DirectorUnavailable("нет OPENAI_API_KEY в .env")
        try:
            r = requests.post(self.cfg.get("endpoint", "https://api.openai.com/v1/responses"), json=body,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              timeout=int(self.cfg.get("timeout_sec", 120)), allow_redirects=False)
        except requests.RequestException as e:
            raise DirectorUnavailable(f"OpenAI недоступен: {redact(str(e))}") from e
        if r.status_code >= 400:
            raise DirectorUnavailable(f"OpenAI HTTP {r.status_code}: {redact(r.text[:400])}")
        resp = r.json()
        if resp.get("status") not in (None, "completed"):
            raise DirectorUnavailable(f"OpenAI: ответ не завершён ({resp.get('status')})")
        return resp


    def _base(self) -> str:
        ep = self.cfg.get("endpoint", "https://api.openai.com/v1/responses")
        return ep.rsplit("/responses", 1)[0]

    def list_models(self) -> list[str]:
        """Бесплатно: GET /v1/models — проверяет ключ и показывает доступные проекту модели."""
        key = secret("OPENAI_API_KEY")
        if not key:
            raise DirectorUnavailable("нет OPENAI_API_KEY в .env")
        try:
            r = requests.get(f"{self._base()}/models", headers={"Authorization": f"Bearer {key}"},
                             timeout=30, allow_redirects=False)
        except requests.RequestException as e:
            raise DirectorUnavailable(f"OpenAI недоступен: {redact(str(e))}") from e
        if r.status_code >= 400:
            raise DirectorUnavailable(f"OpenAI HTTP {r.status_code}: {redact(r.text[:400])}")
        return sorted(m.get("id", "") for m in r.json().get("data") or [])

    def ping(self) -> dict:
        """Минимальный платный запрос (десятки токенов): Responses API + JSON-схема отвечают."""
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "reply": {"type": "string"}},
                  "required": ["ok", "reply"], "additionalProperties": False}
        body = {"model": self.cfg["model"], "instructions": "Ты — проверка связи. Ответь кратко.",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "Ответь ok=true и reply='на связи'."}]}],
                "store": False, "max_output_tokens": 400,
                "text": {"format": {"type": "json_schema", "name": "ping", "schema": schema, "strict": True}}}
        resp = self.send(body)
        return {"id": resp.get("id"), "model": resp.get("model"), "text": output_text(resp), "usage": resp.get("usage")}


class MockDirector:
    """Dry-run: правдоподобный ответ по схеме без обращения к OpenAI (никаких денег, никакой сети)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def configured(self) -> tuple[bool, str]:
        return True, "mock (dry-run)"

    def send(self, body: dict) -> dict:
        if "text" not in body:   # свободный вопрос (studio director ask)
            ans = "[MOCK] Понял вопрос. Рекомендую сначала проверить одну сцену, платное — только после вашего «утверждаю»."
            return {"id": "mock_ask", "status": "completed", "model": "mock",
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": ans}]}], "usage": {}}
        name = body["text"]["format"]["name"]
        text = body["input"][0]["content"][0]["text"]
        meta = json.loads(text.split("```json", 1)[1].split("```", 1)[0]) if "```json" in text else {}
        if name == "script_review":
            scenes = meta.get("scenes", [])
            out = {"status": "approved", "overall_notes": "[MOCK] Структура 5→1 читается, хук в первые 3 с, CTA чистый.",
                   "scene_notes": [{"scene_id": s["id"], "status": "approved", "notes": "[MOCK] ок"} for s in scenes],
                   "requires_user_approval": []}
            if scenes:
                out["scene_notes"][min(3, len(scenes) - 1)].update(status="revise", notes="[MOCK] сократить фразу на 1 с")
        elif name == "scene_review":
            out = {"scene_id": meta.get("scene_id", "?"), "status": "revise",
                   "identity": {"pass": True, "confidence": "low", "notes": "[MOCK] уверенность низкая — нужен ручной QC"},
                   "motion": {"pass": False, "notes": "[MOCK] голова двигается слишком сильно"},
                   "composition": {"pass": True, "notes": "[MOCK] ок"},
                   "issues": ["[MOCK] резкий поворот головы на 2.3 с"],
                   "revision_prompt": "[MOCK] slower head turn, subtle blink, calm breathing, ears stay folded, static camera",
                   "requires_user_approval": ["платная перегенерация сцены"]}
        else:
            out = {"final_score": 8.0, "status": "revise",
                   "required_changes": [{"timestamp": "00:03", "scene_id": "s02", "change": "[MOCK] стык слишком резкий"}],
                   "optional_improvements": [{"timestamp": "00:40", "scene_id": "s11", "change": "[MOCK] иконки на 0.2 с раньше"}],
                   "checks": {k: "[MOCK] ок" for k in ["hook_0_3s", "pacing", "scene_duration", "repetition",
                                                       "character_consistency", "text_readability", "cta", "soundtrack",
                                                       "transitions", "retention_potential"]},
                   "requires_user_approval": []}
        return {"id": f"mock_{name}", "status": "completed", "model": "mock",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(out, ensure_ascii=False)}]}],
                "usage": {"input_tokens": 0, "output_tokens": 0}}
