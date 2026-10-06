import base64
import json
import os
import shutil
import sys
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
        # длиннее 60 с — только осознанное решение владельца, записанное в target_duration сценария
        assert 20 <= s.planned_duration <= max(60, s.target_duration + 5), p.name


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
    assert k.estimate_usd(req, pricing) == pytest.approx(0.3 * 10 * pricing["kling"]["unit_usd"])
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
    assert all(sum(words[i] != "—" for i in g) <= 3 for g in groups)   # тире словом не считается
    assert groups[0][-1] == 2  # разрыв после вопроса
    # одно слово на долю секунды («геосервисы» за 0.17 с) склеивается с соседней фразой
    t = "Второе — Яндекс Карты и геосервисы. Для студии это важно."
    ws, tt = [], 0.0
    for w in t.split():
        dd = 0.17 if w == "геосервисы." else 0.3
        ws.append([w, tt, tt + dd]); tt += dd
    cs = scene_cues("s08", t, 0, {"text": t, "words": ws, "duration": tt}, tt, 5, 26)
    assert all(c.end - c.start >= 0.7 for c in cs) and "Карты и геосервисы." in cs[0].text
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

    pcm = tmp_path / "t.pcm"  # сырой s16le 48 кГц, как запрашивает адаптер
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1.5",
                    "-ar", "48000", "-ac", "1", "-f", "s16le", str(pcm)], check=True)
    data = pcm.read_bytes()
    half = len(data) // 2 // 2 * 2
    # Два чанка потока — должны склеиться в один непрерывный сигнал
    lines = [
        {"result": {"audioChunk": {"data": base64.b64encode(data[:half]).decode()}, "wordTimings": [
            {"word": "привет", "startMs": "0", "lengthMs": "500"}]}},
        {"result": {"audioChunk": {"data": base64.b64encode(data[half:]).decode()}, "wordTimings": [
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


def test_dotenv_with_bom(tmp_path, monkeypatch):
    from studio.config import load_dotenv
    monkeypatch.delenv("BOM_TEST_KEY", raising=False)
    (tmp_path / ".env").write_bytes("﻿BOM_TEST_KEY=abc\n".encode("utf-8"))
    load_dotenv(tmp_path / ".env")
    assert os.environ.get("BOM_TEST_KEY") == "abc"
    monkeypatch.delenv("BOM_TEST_KEY")


def test_status_normalization():
    from studio.integrations.base import normalize_status
    assert normalize_status("succeed") == "succeeded"
    assert normalize_status("COMPLETED") == "succeeded"
    assert normalize_status("queued") == "submitted"
    assert normalize_status("weird") == "processing"
    assert normalize_status("canceled") == "failed"


def test_regenerate_blocked_while_job_active(settings):
    from studio.character.library import CharacterLibrary
    from studio.generation.runner import Runner, plan
    from studio.generation.voice import generate_voice
    from studio.project import create_project

    CharacterLibrary(settings).set_status("office_hoodie_laptop", "approved")
    script = template()
    for sc in script.scenes:
        if sc.id != "s04":
            sc.generator = "local"
            sc.local = sc.local or {"kind": "character"}
    proj = create_project(script, settings, episode_id="ep-regen")
    proj.approve_script()
    settings.data["override_generator"] = "mock"
    db = DB(settings.db_path)
    generate_voice(proj, db, provider_name="mock", assume_yes=True)
    key = plan(proj, proj.load_script())[0].key
    db.create_job(episode="ep-regen", scene_id="s04", provider="mock", kind="image2video", model="m", params={},
                  idempotency_key=key, est_cost_usd=0, status="unknown")
    Runner(proj, db).generate(assume_yes=True, regenerate=["s04"], wait=False)
    assert len(db.jobs_for("ep-regen", "s04", "image2video")) == 1  # новая задача не создана


def test_tts_spend_counts_in_budget(settings, monkeypatch):
    from studio.generation.voice import generate_voice
    from studio.integrations import tts as tts_mod
    from studio.project import create_project

    class PaidMock(tts_mod.MockTTS):
        paid = True

        def estimate_usd(self, text, preset, pricing):
            return 0.5

    monkeypatch.setattr("studio.generation.voice.tts_provider", lambda name, s: PaidMock(s))
    proj = create_project(template(), settings, episode_id="ep-tts")
    proj.approve_script()
    db = DB(settings.db_path)
    generate_voice(proj, db, provider_name="yandex", assume_yes=True)
    assert db.spend(episode="ep-tts") == pytest.approx(0.5 * 5)


def test_security_guards(settings, tmp_path, monkeypatch):
    from studio.editing.local_scenes import LocalRenderError, resolve_image
    from studio.integrations.base import NotConfiguredError
    from studio.project import ProjectError, open_project

    proj = tmp_path / "proj"
    (proj / "imports").mkdir(parents=True)
    (settings.root / ".env").write_text("KLING_API_KEY=secret\n", encoding="utf-8")
    # нельзя выйти за пределы проекта и отправить .env под видом картинки
    for bad in ("../../.env", str(settings.root / ".env"), "../proj/../../.env"):
        with pytest.raises(LocalRenderError):
            resolve_image(proj, settings, bad)
    (proj / "imports" / "fake.png").write_text("not an image", encoding="utf-8")
    with pytest.raises(LocalRenderError):
        resolve_image(proj, settings, "fake.png")
    # ключ с переводом строки не утекает в текст ошибки
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_testsecret123\n")
    assert "sk_testsecret123" not in redact(f"Invalid header value {'sk_testsecret123' + chr(10)!r}")
    assert "abc123def" not in redact("Authorization: Api-Key abc123def")
    # недопустимые id
    with pytest.raises(ScriptError):
        Script.from_dict({"id": "x", "title": "t", "scenes": [{"id": "../../x", "type": "talking", "duration": 3}]})
    with pytest.raises(ProjectError):
        open_project("../etc", settings)
    monkeypatch.setenv("KLING_API_BASE", "http://evil.example.com")
    with pytest.raises(NotConfiguredError):
        KlingProvider(settings)


def test_voice_samples_command(settings, tmp_path, monkeypatch):
    import subprocess
    from studio import cli
    from studio.integrations import tts as tts_mod

    pcm = tmp_path / "t.pcm"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1",
                    "-ar", "48000", "-ac", "1", "-f", "s16le", str(pcm)], check=True)
    chunk = json.dumps({"result": {"audioChunk": {"data": base64.b64encode(pcm.read_bytes()).decode()}}})
    calls = []

    class Resp:
        def __init__(self, ok):
            self.status_code = 200 if ok else 400
            self.text = chunk if ok else '{"error":"unknown voice"}'

    def fake_post(url, headers, json, timeout):  # noqa: A002
        voice = json["hints"][0]["voice"]
        calls.append((voice, headers))
        return Resp(voice != "badvoice")

    monkeypatch.setenv("YANDEX_API_KEY", "yc-key-123456")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "aje-wrong-folder")
    monkeypatch.setattr(tts_mod.requests, "post", fake_post)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    rc = cli.main(["voice-samples", "--voices", "alena,badvoice,jane", "--pitch", "150", "--variants", "1.3", "--yes"])
    assert rc == 0
    files = sorted(p.name for p in (settings.data_dir / "voice_samples").glob("*.wav"))
    assert files == ["alena_p150_s1.1.wav", "alena_p150_s1.1_cat1.3.wav", "jane_p150_s1.1.wav",
                     "jane_p150_s1.1_cat1.3.wav"]
    assert len(calls) == 3  # варианты тона делаются локально, без новых платных запросов
    assert all("x-folder-id" not in h for _, h in calls)  # с API-ключом folder id не отправляется


def test_reference_status_overlay(settings):
    from studio.character.library import CharacterLibrary
    from studio import cli
    lib = CharacterLibrary(settings)
    yaml_before = lib.path.read_text(encoding="utf-8")
    lib.set_status("office_hoodie_laptop", "approved")
    assert lib.get("office_hoodie_laptop")["status"] == "approved"
    assert lib.path.read_text(encoding="utf-8") == yaml_before  # отслеживаемый YAML не меняется
    lib.set_status("office_hoodie_laptop", "pending")
    import pytest as _p
    with _p.raises(Exception):
        lib.set_status("no_such_ref", "approved")


def test_redact_boxes_hide_content():
    from PIL import Image, ImageDraw
    from studio.editing.local_scenes import redact_boxes
    img = Image.new("RGB", (400, 200), "white")
    ImageDraw.Draw(img).text((20, 20), "CLIENT-SECRET-DOMAIN.RU", fill="black")
    out = redact_boxes(img, [[0, 0, 400, 100]])
    region = out.crop((0, 0, 400, 100))
    assert region.getextrema() != img.crop((0, 0, 400, 100)).getextrema() or len(set(region.getdata())) < 50
    assert out.crop((0, 100, 400, 200)).tobytes() == img.crop((0, 100, 400, 200)).tobytes()


def test_kling_motion_lipsync_chain(settings, tmp_path, monkeypatch):
    import subprocess
    from studio.integrations import kling as kmod

    audio = tmp_path / "s01.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=3",
                    "-ar", "48000", "-ac", "1", str(audio)], check=True)
    calls = []

    def fake_http(method, url, headers, json_body=None, params=None, timeout=60, retries=4, safe_to_retry=True):
        calls.append((method, url.split("klingai.com")[-1], params, json_body))
        if url.endswith("/tasks"):
            return {"code": 0, "data": [{"id": "i2v1", "status": "succeeded",
                                         "outputs": [{"type": "video", "id": "vid42", "url": "http://x/a.mp4"}]}]}
        if url.endswith("/identify-face"):
            assert json_body == {"video_id": "vid42"}
            return {"code": 0, "data": {"session_id": "sess1", "face_data": [{"face_id": "0"}]}}
        if url.endswith("/advanced-lip-sync"):
            fc = json_body["face_choose"][0]
            assert fc["sound_end_time"] == 3000 and json_body["external_task_id"] == "job1-ls"
            return {"code": 0, "data": {"task_id": "ls9"}}
        if "/advanced-lip-sync/ls9" in url:
            return {"code": 0, "data": {"task_id": "ls9", "task_status": "succeed",
                                        "task_result": {"videos": [{"url": "http://x/final.mp4"}]}}}
        raise AssertionError(url)

    monkeypatch.setenv("KLING_API_KEY", "api-key-kling-test")
    monkeypatch.setattr(kmod, "http_json", fake_http)
    k = kmod.KlingProvider(settings)
    ctx = {"audio": str(audio), "audio_seconds": 3.0, "job_id": "job1"}
    st = k.poll("chain:i2v:i2v1", "motion_lipsync", ctx)
    assert st.status == "processing" and st.next_task_id == "chain:ls:ls9"
    st2 = k.poll(st.next_task_id, "motion_lipsync", ctx)
    assert st2.status == "succeeded" and st2.video_url == "http://x/final.mp4"
    assert sum(1 for c in calls if c[1].endswith("/advanced-lip-sync")) == 1  # lip-sync отправлен один раз

    pricing = settings.load_yaml("config/pricing.yaml")
    from studio.integrations.base import VideoRequest
    req = VideoRequest(kind="motion_lipsync", model="kling-2.6", duration=3.3, resolution="720p",
                       extra={"audio_seconds": 3.0})
    unit = pricing["kling"]["unit_usd"]
    assert k.estimate_usd(req, pricing) == pytest.approx((0.3 * 5 + 0.5 + 0.05) * unit)


def test_web_panel_api(settings):
    import threading
    import time
    import urllib.request
    from http.server import ThreadingHTTPServer
    from studio.character.library import CharacterLibrary
    from studio.web.server import App, make_handler

    for r in CharacterLibrary(settings).references():
        CharacterLibrary(settings).set_status(r["id"], "approved")
    settings.data["override_generator"] = "mock"
    app = App(settings)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def get(u):
        return json.loads(urllib.request.urlopen(base + u).read())

    def post(u, body, token=app.token):
        req = urllib.request.Request(base + u, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-Token": token})
        try:
            return json.loads(urllib.request.urlopen(req).read())
        except urllib.error.HTTPError as e:
            return {"code": e.code, **json.loads(e.read() or b"{}")}

    try:
        assert "МяуРкетинг" in urllib.request.urlopen(base + "/").read().decode()
        assert post("/api/new", {"template": "01-direct-budget"}, token="wrong")["code"] == 403
        ep = post("/api/new", {"template": "01-direct-budget"})["id"]
        d = get(f"/api/episode/{ep}")
        assert d["status"] == "draft" and len(d["scenes"]) == 5 and "video_usd" in d["estimate"]
        # превью платных сцен без `reference` — первый утверждённый референс (его же возьмёт генерация)
        assert all(sc["reference_file"] for sc in d["scenes"] if sc["paid"])
        # платное действие без подтверждения запрещено
        assert "error" in post(f"/api/episode/{ep}/action", {"action": "voice"})
        assert post(f"/api/episode/{ep}/action", {"action": "approve_script"})["ok"]
        import studio.generation.voice as vmod
        from studio.integrations.tts import MockTTS
        orig = vmod.tts_provider
        vmod.tts_provider = lambda name, s: MockTTS(s)
        try:
            t = post(f"/api/episode/{ep}/action", {"action": "voice", "confirm": True})["task"]
            for _ in range(100):
                if get(f"/api/task/{t}")["status"] != "running":
                    break
                time.sleep(0.2)
            assert get(f"/api/task/{t}")["status"] == "done", get(f"/api/task/{t}")
        finally:
            vmod.tts_provider = orig
        d = get(f"/api/episode/{ep}")
        assert all(sc["has_audio"] for sc in d["scenes"])
        # медиа отдаются только из папки проекта
        r = urllib.request.urlopen(f"{base}/media/{ep}/audio/s01.wav")
        assert r.status == 200 and r.headers["Content-Type"] == "audio/wav"
        size = int(r.headers["Content-Length"])
        # плеер браузера запрашивает диапазоны; диапазон за концом файла — 416, а не битый ответ
        rq = urllib.request.Request(f"{base}/media/{ep}/audio/s01.wav", headers={"Range": "bytes=0-99"})
        r = urllib.request.urlopen(rq)
        assert r.status == 206 and len(r.read()) == 100 and r.headers["Content-Range"] == f"bytes 0-99/{size}"
        rq = urllib.request.Request(f"{base}/media/{ep}/audio/s01.wav", headers={"Range": f"bytes={size + 10}-"})
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(rq)
        assert ei.value.code == 416
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{base}/media/{ep}/..%2F..%2Fconfig%2Fstudio.yaml")
    finally:
        httpd.shutdown()


def test_lipsync_no_human_falls_back_to_animation(settings, monkeypatch):
    from studio.integrations import kling as kmod
    from studio.integrations.base import ProviderError

    def fake_http(method, url, headers, json_body=None, params=None, timeout=60, retries=4, safe_to_retry=True):
        if url.endswith("/tasks"):
            return {"code": 0, "data": [{"id": "i1", "status": "succeeded",
                                         "outputs": [{"type": "video", "id": "v1", "url": "http://x/anim.mp4"}]}]}
        if url.endswith("/identify-face"):
            raise ProviderError("HTTP 400: {'code': 1201, 'message': 'The model did not detect a human'}",
                                status=400, code=1201)
        raise AssertionError(url)

    monkeypatch.setenv("KLING_API_KEY", "api-key-kling-test")
    monkeypatch.setattr(kmod, "http_json", fake_http)
    st = kmod.KlingProvider(settings).poll("chain:i2v:i1", "motion_lipsync", {"audio": "x", "job_id": "j"})
    assert st.status == "succeeded" and st.video_url == "http://x/anim.mp4" and "Lip Sync" in st.message


def test_stale_job_does_not_overwrite_newer_scene(settings, tmp_path):
    from studio.generation.runner import Runner
    from studio.project import create_project
    proj = create_project(template(), settings, episode_id="ep-stale")
    db = DB(settings.db_path)
    old = db.create_job(episode="ep-stale", scene_id="s01", provider="mock", kind="motion_lipsync", model="m",
                        params={}, idempotency_key="a", est_cost_usd=0, status="processing")
    import time; time.sleep(1.1)
    db.create_job(episode="ep-stale", scene_id="s01", provider="mock", kind="avatar", model="m",
                  params={}, idempotency_key="b", est_cost_usd=0, status="succeeded")
    db.update_job(old, status="succeeded", result_url="file:///nonexistent.mp4")
    assert Runner(proj, db)._download(db.get_job(old)) is None


def test_higgsfield_adapter_protocol(tmp_path, monkeypatch):
    """Адаптер Higgsfield: авторизация Key id:secret, загрузка файла, тело Seedance без звука, разбор статусов."""
    from studio.integrations import higgsfield as hf
    from studio.integrations.base import VideoRequest

    monkeypatch.setenv("HIGGSFIELD_API_KEY", "kid")
    monkeypatch.setenv("HIGGSFIELD_API_SECRET", "ksec")
    calls = []

    def fake_http(method, url, *, headers, json_body=None, **kw):
        calls.append((method, url, headers.get("Authorization"), json_body, kw.get("safe_to_retry", True)))
        if url.endswith("/files/generate-upload-url"):
            return {"public_url": "https://cdn/x.png", "upload_url": "https://up/x"}
        if url.endswith("/status"):
            return {"status": "completed", "video": {"url": "https://cdn/v.mp4"}}
        return {"request_id": "r1", "status_url": "s", "cancel_url": "c"}

    class Resp:
        status_code = 200
        text = ""

    monkeypatch.setattr(hf, "http_json", fake_http)
    monkeypatch.setattr(hf.requests, "put", lambda *a, **k: Resp())
    img = tmp_path / "cat.png"
    img.write_bytes(b"png")
    p = hf.HiggsfieldProvider()
    req = VideoRequest(kind="image2video", model="bytedance/seedance-2.5/image-to-video", prompt="cat", image=img,
                       duration=4.6, resolution="720p")
    assert p.billed_duration(req) == 5
    assert p.estimate_usd(req, {"higgsfield": {"models": {req.model: {"usd_per_second": {"720p": 0.2}}}}}) == 1.0
    assert p.submit(req) == "r1"
    method, url, auth, body, safe = calls[-1]
    assert url == "https://api.higgsfield.ai/bytedance/seedance-2.5/image-to-video" and auth == "Key kid:ksec"
    assert body["image_url"] == "https://cdn/x.png" and body["generate_audio"] is False and body["duration"] == 5
    assert safe is False   # платный запрос не повторяется вслепую
    st = p.poll("r1", "image2video")
    assert st.status == "succeeded" and st.video_url == "https://cdn/v.mp4"


def test_limit_errors_do_not_block_retry():
    """Отказ из-за лимита одновременных задач (Kling 1303 / HTTP 429) — не ошибка сцены: повтор не блокируется."""
    from studio.generation.runner import is_limit_error
    assert is_limit_error("HTTP 429: {'code': 1303, 'message': 'parallel task over resource pack limit'}")
    assert is_limit_error("отложено (лимит задач): HTTP 429")
    assert not is_limit_error("HTTP 400: {'code': 1201, 'message': 'The model did not detect a human'}")
    assert not is_limit_error(None)


def test_director_brief_locks_and_final_gate(settings, tmp_path):
    """Бриф директора: только approved; locked-текст нельзя менять; final нельзя без QC кота и при revise директора."""
    import json as _json
    import yaml as _yaml
    from studio.director import brief as br, state
    from studio.director.openai_client import MockDirector, build_request, output_text
    from studio.character.library import CharacterLibrary
    s = settings
    lib = CharacterLibrary(s)
    ref = lib.references()[0]["id"]
    lib.set_status(ref, "approved")
    b = {"project": "t1", "status": "draft", "scenes": []}
    assert any("approved" in e for e in br.validate_brief(b, s))
    bf = tmp_path / "b.yaml"
    bf.write_text(_yaml.safe_dump({"project": "t1", "status": "approved", "goal": {"cta": "Напиши ФИТНЕС"}, "scenes": [
        {"id": "s01", "location": "office", "asset": ref, "type": "talking", "generator": "kling", "duration": 4,
         "voiceover": "Привет, это тест для проверки.", "motion": "talks to camera", "locked_text": True}]},
        allow_unicode=True), encoding="utf-8")
    proj, script, errs = br.import_brief(bf, s, episode_id="t1-ep")
    assert not errs and "ears stay folded, static camera" in script.scenes[0].prompts["kling"]
    sc = proj.load_script()
    sc.scenes[0].voiceover = "Другой текст"
    proj.save_script(sc)
    assert br.check_locks(proj)
    st = state.load(proj)
    st["s01"].update(status="review", versions=1, director={"status": "revise", "version": 1})
    state.save(proj, st)
    import pytest
    with pytest.raises(state.StateError):
        state.set_status(proj, "s01", "final")
    body = build_request({"model": "m"}, instructions="i", text="```json\n{\"scene_id\": \"s01\"}\n```", images=[],
                         schema_name="scene_review", schema={}, previous_response_id=None)
    assert body["text"]["format"]["type"] == "json_schema" and body["input"][0]["content"][0]["type"] == "input_text"
    data = _json.loads(output_text(MockDirector({}).send(body)))
    assert data["scene_id"] == "s01" and data["status"] in ("approved", "revise")


def test_director_ping_without_key(settings, monkeypatch, capsys):
    from studio import cli
    shutil.copytree(ROOT / "director_bridge/config", settings.root / "director_bridge/config")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("studio.director.openai_client.secret", lambda name: None)
    rc = cli._director_ping(type("A", (), {"yes": True})(), settings)
    assert rc == 1
    assert "OPENAI_API_KEY" in capsys.readouterr().out


def test_director_chat_render(settings):
    from studio.director import chat
    from studio.project import create_project
    project = create_project(template(), settings)
    chat.append(project, "owner", "вопрос <b>")
    chat.append(project, "director", "ответ")
    out = chat.render(project)
    page = out.read_text(encoding="utf-8")
    assert "вопрос &lt;b&gt;" in page and 'class="msg director"' in page


def test_bot_init_data_signature():
    import hashlib, hmac, json, time
    from urllib.parse import urlencode
    from studio.bot.app import check_init_data
    token, owner = "123:ABC", 42
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": owner}), "query_id": "q"}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    good = urlencode(fields)
    assert check_init_data(good, token, owner)
    assert not check_init_data(good, token, 7)            # чужой пользователь
    assert not check_init_data(good, "999:XYZ", owner)    # чужой бот
    assert not check_init_data(good.replace("q", "x"), token, owner)


def test_bot_producer_tools_and_paid_guard(settings, monkeypatch):
    import yaml
    from types import SimpleNamespace as NS
    from studio.bot import agent
    assert agent.check_free(["generate", "ep"]).startswith("generate")
    assert agent.check_free(["pipeline", "ep"]) is not None
    assert agent.check_free(["qa", "ep"]) is None
    assert agent.check_free(["qa", "ep", "--yes"]) is not None
    shutil.copy(ROOT / "config/agents.yaml", settings.root / "config/agents.yaml")
    cfg = yaml.safe_load((settings.root / "config/agents.yaml").read_text(encoding="utf-8"))

    def blk(**kw):
        return NS(**kw, model_dump=lambda exclude_none=True, kw=kw: dict(kw))
    replies = [NS(stop_reason="tool_use", content=[blk(type="tool_use", id="t1", name="studio", input={"args": ["generate", "x"]})]),
               NS(stop_reason="end_turn", content=[blk(type="text", text="Готово")])]
    seen = []

    class FakeBeta:
        def create(self, **kw):
            seen.append(kw)
            return replies.pop(0)
    fake = NS(beta=NS(messages=FakeBeta()), messages=FakeBeta())
    monkeypatch.setitem(sys.modules, "anthropic", NS(Anthropic=lambda: fake, AuthenticationError=Exception,
                        PermissionDeniedError=Exception, RateLimitError=Exception, APIStatusError=Exception,
                        APIConnectionError=Exception))
    host = NS(notify=lambda t: None)
    p = agent.Producer(settings, cfg, host, settings.root / "data/bot/h.json")
    assert p.chat("собери ролик") == "Готово"
    tool_result = seen[1]["messages"][2]["content"][0]["content"]   # user → tool_use → tool_result
    assert tool_result.startswith("ОТКАЗ") and "request_paid" in tool_result   # платное без кнопки не запускается
    assert seen[0]["fallbacks"] == "default" and seen[0]["model"] == cfg["producer"]["model"]


def test_salute_tts_protocol(settings, monkeypatch, tmp_path):
    import io
    import wave
    from types import SimpleNamespace as NS
    from studio.integrations import tts
    monkeypatch.setenv("SALUTE_AUTH_KEY", "QmFzaWNLZXk=")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b"\x00\x00" * 24000)
    calls = []

    def fake_post(url, **kw):
        calls.append((url, kw))
        if "oauth" in url:
            return NS(status_code=200, json=lambda: {"access_token": "TOK", "expires_at": 4102444800000}, text="")
        return NS(status_code=200, content=buf.getvalue(), text="")
    monkeypatch.setattr(tts.requests, "post", fake_post)
    p = tts.SaluteTTS(settings)
    res = p.synthesize("Мяу, привет", {"voice": "Bys_24000", "speed": 1.0}, tmp_path / "a.wav")
    p.synthesize("Ещё раз", {"voice": "Bys_24000"}, tmp_path / "b.wav")
    oauth = [c for c in calls if "oauth" in c[0]]
    assert len(oauth) == 1                                         # токен переиспользуется
    assert oauth[0][1]["headers"]["Authorization"] == "Basic QmFzaWNLZXk="
    assert oauth[0][1]["data"] == {"scope": "SALUTE_SPEECH_PERS"} and oauth[0][1]["headers"]["RqUID"]
    synth = calls[1]
    assert synth[1]["params"] == {"format": "wav16", "voice": "Bys_24000"}
    assert synth[1]["headers"]["Authorization"] == "Bearer TOK"
    assert synth[1]["headers"]["Content-Type"] == "application/text"
    assert abs(res.duration - 1.0) < 0.1 and [w[0] for w in res.words] == ["Мяу,", "привет"]
    assert 0 < res.words[0][1] < res.words[1][1] < res.duration
