import base64
import json
import os
import shutil
from pathlib import Path

import pytest

from studio.config import get_settings, redact
from studio.costs.budget import Budget, BudgetError
from studio.db import DB
from studio.editing.subtitles import chunk_words, scene_cues
from studio.integrations.kling import KlingProvider, make_jwt
from studio.integrations.base import VideoRequest
from studio.integrations.tts import words_from_alignment
from studio.models import Script, ScriptError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def settings(tmp_path):
    """Изолированная копия проекта: конфиги, персонаж, шаблоны."""
    for d in ("config", "assets/character", "assets/templates"):
        shutil.copytree(ROOT / d, tmp_path / d)
    (tmp_path / "assets/fonts").mkdir(parents=True)
    os.environ["STUDIO_ROOT"] = str(tmp_path)
    s = get_settings(tmp_path, reload=True)
    yield s
    os.environ.pop("STUDIO_ROOT", None)


def template(name="01-direct-budget") -> Script:
    return Script.load(ROOT / "assets/templates/scripts" / f"{name}.yaml")


def test_templates_valid():
    for p in (ROOT / "assets/templates/scripts").glob("*.yaml"):
        s = Script.load(p)
        s.validate()
        assert 20 <= s.planned_duration <= 35, p.name


def test_script_rejects_unknown_fields():
    with pytest.raises(ScriptError):
        Script.from_dict({"id": "x", "title": "t", "oops": 1, "scenes": []})
    s = template()
    s.scenes[0].generator = "kling"
    s.scenes[0].prompts = {}
    s.scenes[0].visual = ""
    with pytest.raises(ScriptError):
        s.validate()


def test_fingerprint_changes_on_edit():
    s = template()
    fp = s.fingerprint()
    s.scenes[1].voiceover += " ещё"
    assert s.fingerprint() != fp


def test_jwt_structure():
    tok = make_jwt("ak", "sk")
    h, p, _sig = tok.split(".")
    payload = json.loads(base64.urlsafe_b64decode(p + "=="))
    assert payload["iss"] == "ak" and payload["exp"] > payload["nbf"]
    assert json.loads(base64.urlsafe_b64decode(h + "=="))["alg"] == "HS256"


def test_kling_pricing_and_durations(settings):
    k = KlingProvider(settings)
    pricing = settings.load_yaml("config/pricing.yaml")
    req = VideoRequest(kind="image2video", model="kling-2.6", duration=7, resolution="720p")
    assert k.billed_duration(req) == 10
    assert k.estimate_usd(req, pricing) == pytest.approx(0.3 * 10 * 0.14)
    av = VideoRequest(kind="avatar", model="avatar", duration=1.0, mode="std")
    assert k.billed_duration(av) == 2.0  # минимум 2 с у Avatar


def test_kling_parse_statuses():
    st = KlingProvider._parse_legacy({"task_id": "1", "task_status": "succeed",
                                      "task_result": {"videos": [{"url": "http://x/v.mp4"}]}})
    assert st.status == "succeeded" and st.video_url.endswith("v.mp4")
    st2 = KlingProvider._parse_new({"id": "2", "status": "processing", "outputs": []})
    assert st2.status == "processing" and not st2.done


def test_redact_hides_secrets(monkeypatch):
    monkeypatch.setenv("KLING_API_KEY", "supersecretvalue123")
    assert "supersecretvalue123" not in redact("error with supersecretvalue123 inside")
    assert "abc.def" not in redact("Authorization: Bearer abc.def")


def test_budget_limits(settings, tmp_path):
    db = DB(tmp_path / "t.sqlite3")
    b = Budget(settings, db)
    b.check("ep", 1.0)
    with pytest.raises(BudgetError):
        b.check("ep", b.per_video + 0.01)
    jid = db.create_job(episode="ep", scene_id="s01", provider="kling", kind="avatar", model="avatar",
                        params={}, idempotency_key="k", est_cost_usd=b.per_video - 0.5, status="submitted")
    assert db.spend(episode="ep") == pytest.approx(b.per_video - 0.5)
    with pytest.raises(BudgetError):
        b.check("ep", 1.0)
    db.update_job(jid, actual_cost_usd=0.1)
    assert db.spend(episode="ep") == pytest.approx(0.1)


def test_subtitle_chunks_and_alignment():
    words = "Директ сливает бюджет? Три причины — за тридцать секунд.".split()
    groups = chunk_words(words, 3, 24)
    assert all(len(g) <= 3 for g in groups)
    assert groups[0][-1] == 2  # разрыв после вопроса
    al = {"characters": list("да нет"), "character_start_times_seconds": [0, .1, .2, .3, .4, .5],
          "character_end_times_seconds": [.1, .2, .3, .4, .5, .6]}
    assert words_from_alignment(al) == [("да", 0, .2), ("нет", .3, .6)]
    cues = scene_cues("s01", "да нет", 10.0, {"text": "да нет", "words": [["да", 0, .2], ["нет", .3, .6]],
                                               "duration": 0.6}, 1, 3, 18)
    assert cues[0].start == 10.0 and " ".join(c.text for c in cues) == "да нет"


def test_approval_and_idempotent_mock_generation(settings):
    from studio.character.library import CharacterLibrary
    from studio.generation.runner import Runner, plan
    from studio.generation.voice import generate_voice
    from studio.project import ProjectError, create_project

    CharacterLibrary(settings).set_status("office_hoodie_laptop", "approved")
    script = template()
    # только одна AI-сцена, остальные локальные — быстрее тест
    for sc in script.scenes:
        if sc.id != "s04":
            sc.generator = "local"
            sc.local = sc.local or {"kind": "character"}
    proj = create_project(script, settings, episode_id="ep-test")
    db = DB(settings.db_path)
    with pytest.raises(ProjectError):
        Runner(proj, db).generate(assume_yes=True)  # без утверждения — нельзя
    proj.approve_script()
    settings.data["override_generator"] = "mock"
    generate_voice(proj, db, provider_name="mock", assume_yes=True)
    jobs1 = Runner(proj, db).generate(assume_yes=True)
    assert len(jobs1) == 1 and jobs1[0]["status"] == "succeeded"
    assert proj.scene_source("s04")
    # повторный запуск ничего не отправляет заново
    Runner(proj, db).generate(assume_yes=True)
    assert len(db.jobs_for("ep-test")) == 1
    # изменение сценария снимает утверждение
    sc = proj.load_script()
    sc.scenes[3].prompts["kling"] += " slowly"
    proj.save_script(sc)
    assert not proj.is_script_approved()
    assert plan(proj, sc)[0].key != db.jobs_for("ep-test")[0]["idempotency_key"]


def test_yandex_tts_parses_stream(tmp_path, monkeypatch, settings):
    import subprocess
    from studio.integrations import tts as tts_mod

    wav = tmp_path / "t.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1.5",
                    "-ar", "22050", str(wav)], check=True)
    b64 = base64.b64encode(wav.read_bytes()).decode()
    lines = [
        {"result": {"audioChunk": {"data": b64}, "wordTimings": [
            {"word": "привет", "startMs": "0", "lengthMs": "500"},
            {"word": "кот", "startMs": "600", "lengthMs": "400"}]}},
    ]
    captured = {}

    class Resp:
        status_code = 200
        text = "\n".join(json.dumps(x) for x in lines)

    def fake_post(url, headers, json, timeout):  # noqa: A002
        captured.update(url=url, headers=headers, body=json)
        return Resp()

    monkeypatch.setenv("YANDEX_API_KEY", "yc-secret-key-123")
    monkeypatch.setattr(tts_mod.requests, "post", fake_post)
    prov = tts_mod.YandexTTS(settings)
    res = prov.synthesize("привет кот", {"voice": "filipp", "speed": 1.1}, tmp_path / "s01.wav")
    assert captured["headers"]["Authorization"] == "Api-Key yc-secret-key-123"
    assert {"voice": "filipp"} in captured["body"]["hints"]
    assert res.words == [("привет", 0.0, 0.5), ("кот", 0.6, 1.0)]
    assert 1.3 < res.duration < 1.7
    assert prov.billing_units("а" * 251) == 2
    assert "yc-secret-key-123" not in redact("key yc-secret-key-123")


def test_short_episode_id_ignores_mock_copy(settings):
    from studio.project import create_project, open_project
    script = template()
    create_project(script, settings, episode_id="episode-001-test")
    create_project(template(), settings, episode_id="episode-001-test--mock")
    assert open_project("episode-001", settings).id == "episode-001-test"
    assert open_project("episode-001-test--mock", settings).id == "episode-001-test--mock"
