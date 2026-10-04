"""Озвучка сцен с кешированием: повторный запуск не тратит символы, если текст и голос не менялись."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import Settings
from ..costs.budget import Budget, CostLine, Estimate
from ..db import DB
from ..integrations import tts_provider
from ..integrations.base import ProviderError
from ..integrations.tts import apply_voice_effect
from ..models import Script
from ..project import Project


def load_preset(settings: Settings, name: str) -> dict:
    presets = settings.load_yaml("config/voices.yaml").get("presets", {})
    if name not in presets:
        raise KeyError(f"Голосовой пресет '{name}' не найден в config/voices.yaml")
    return presets[name]


def voice_key(text: str, preset: dict, provider: str) -> str:
    blob = json.dumps({"t": text, "p": preset, "prov": provider}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def meta_path(project: Project, scene_id: str) -> Path:
    return project.dir("audio") / f"{scene_id}.json"


def scene_voice_meta(project: Project, scene_id: str) -> dict | None:
    p = meta_path(project, scene_id)
    if not p.exists() or not project.scene_audio(scene_id).exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def estimate_voice(project: Project, script: Script, provider_name: str, pricing: dict) -> Estimate:
    settings = project.settings
    prov = tts_provider(provider_name, settings)
    preset = load_preset(settings, script.voice_preset)
    est = Estimate(episode=project.id)
    for s in script.scenes:
        if not s.voiceover.strip():
            continue
        key = voice_key(s.voiceover, preset, provider_name)
        meta = scene_voice_meta(project, s.id)
        reused = bool(meta and meta.get("key") == key)
        usd = prov.estimate_usd(s.voiceover, preset, pricing) if prov.paid else 0.0
        est.lines.append(CostLine(s.id, provider_name, "tts", preset.get("model_id", ""),
                                  f"{len(s.voiceover)} симв.", usd, reused=reused,
                                  verified_price=bool(pricing.get(provider_name, {}).get("verified", True))))
    return est


def generate_voice(project: Project, db: DB, *, provider_name: str | None = None, scenes: list[str] | None = None,
                   assume_yes: bool = False, force: bool = False) -> dict[str, dict]:
    settings = project.settings
    script = project.require_approved()
    provider_name = provider_name or settings.get("providers.tts", "elevenlabs")
    prov = tts_provider(provider_name, settings)
    ok, why = prov.configured()
    if not ok:
        raise RuntimeError(f"TTS '{provider_name}' не настроен: {why}")
    preset = load_preset(settings, script.voice_preset)
    pricing = settings.load_yaml("config/pricing.yaml")

    est = estimate_voice(project, script, provider_name, pricing)
    todo = [l for l in est.lines if (force or not l.reused) and (not scenes or l.scene_id in scenes)]
    if prov.paid and todo:
        amount = round(sum(l.usd for l in todo), 4)
        print(est.table())
        budget = Budget(settings, db)
        for w in budget.check(project.id, 0 if pricing.get(provider_name, {}).get("subscription_based") else amount):
            print(w)
        how = "из лимита подписки" if pricing.get(provider_name, {}).get("subscription_based") else "оплата по факту"
        if not budget.confirm(f"Озвучка {len(todo)} фрагментов через {provider_name}: ~${amount:.3f} ({how}).",
                              assume_yes):
            raise RuntimeError("Озвучка отменена пользователем")

    results: dict[str, dict] = {}
    voiced = [s for s in script.scenes if s.voiceover.strip()]
    for i, s in enumerate(voiced):
        if scenes and s.id not in scenes:
            continue
        key = voice_key(s.voiceover, preset, provider_name)
        meta = scene_voice_meta(project, s.id)
        if meta and meta.get("key") == key and not force:
            results[s.id] = meta
            continue
        dest = project.scene_audio(s.id)
        kwargs = {}
        if provider_name == "elevenlabs":
            kwargs = {"previous_text": voiced[i - 1].voiceover if i > 0 else "",
                      "next_text": voiced[i + 1].voiceover if i + 1 < len(voiced) else ""}
        usd = prov.estimate_usd(s.voiceover, preset, pricing) if prov.paid else 0.0
        if pricing.get(provider_name, {}).get("subscription_based"):
            usd = 0.0  # уже оплачено подпиской — в бюджет API не добавляем
        job_id = None
        if prov.paid:
            # Запись до запроса — расход на озвучку учитывается в месячном лимите и лимите ролика
            job_id = db.create_job(episode=project.id, scene_id=s.id, provider=provider_name, kind="tts",
                                   model=preset.get("model_id") or preset.get("voice"), params={"chars": len(s.voiceover)},
                                   idempotency_key=f"tts-{key}", est_cost_usd=usd, status="submitting")
        try:
            res = prov.synthesize(s.voiceover, preset, dest, **kwargs)
        except ProviderError as e:
            if job_id:  # явный отказ сервера (HTTP-ошибка) — не списано; обрыв связи — считаем оплаченным
                db.update_job(job_id, status="failed", error=str(e), paid=0 if e.status else 1)
            raise
        fx = preset.get("effect") or {}
        if fx.get("pitch"):
            apply_voice_effect(dest, float(fx["pitch"]), fx.get("formant", "shifted"))
        meta = {"key": key, "provider": provider_name, "duration": round(res.duration, 3),
                "words": [list(w) for w in res.words], "characters": res.characters, "text": s.voiceover}
        meta_path(project, s.id).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        if job_id:
            db.update_job(job_id, status="succeeded", result_path=str(dest.relative_to(project.path)))
            db.add_cost(amount_usd=usd, kind="estimated", provider=provider_name, episode=project.id,
                        job_id=job_id, note=f"tts {s.id} {res.characters} симв.")
        db.log("tts", {"scene": s.id, "provider": provider_name, "duration": res.duration}, episode=project.id)
        results[s.id] = meta
    if all(scene_voice_meta(project, s.id) for s in voiced):
        if project.status == "approved":
            project.set_status("voiced")
    return results
