"""Техническая проверка готового ролика.

Проверяется только то, что можно измерить. Внешность кота, мимика, произношение и качество
монтажа НЕ оцениваются автоматически — для них формируется чек-лист ручной проверки.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..editing import ffmpeg
from ..editing.subtitles import read_srt_text
from ..project import Project


@dataclass
class Check:
    name: str
    status: str            # pass | fail | warn | manual
    detail: str = ""
    scene: str | None = None


@dataclass
class Report:
    episode: str
    checks: list[Check] = field(default_factory=list)

    def add(self, *a, **kw) -> None:
        self.checks.append(Check(*a, **kw))

    @property
    def passed(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)

    def text(self) -> str:
        icon = {"pass": "OK  ", "fail": "FAIL", "warn": "WARN", "manual": "РУЧН"}
        lines = [f"Техническая проверка {self.episode}:"]
        for c in self.checks:
            sc = f" [{c.scene}]" if c.scene else ""
            lines.append(f"  [{icon[c.status]}] {c.name}{sc}: {c.detail}")
        auto = "пройдена" if self.passed else "НЕ пройдена"
        lines.append(f"Автоматическая проверка {auto}. Пункты [РУЧН] требуют вашего просмотра — "
                     "автоматически они не проверялись.")
        return "\n".join(lines)


def normalize_text(s: str) -> list[str]:
    s = s.lower().replace("ё", "е")
    return re.findall(r"[a-zа-я0-9%₽$]+", s)


def scene_at(timeline: list[dict], t: float) -> str | None:
    for it in timeline:
        if it["start"] <= t < it["start"] + it["duration"]:
            return it["scene"]
    return timeline[-1]["scene"] if timeline else None


def run_qa(project: Project) -> Report:
    s = project.settings
    rep = Report(project.id)
    out = project.final_video
    if not out.exists() or out.stat().st_size < 10_000:
        rep.add("Файл", "fail", f"нет итогового файла или он пустой: {out}")
        return rep
    try:
        info = ffmpeg.probe(out)
    except ffmpeg.FFmpegError as e:
        rep.add("Файл", "fail", f"ffprobe не читает файл: {e}")
        return rep
    rep.add("Файл", "pass", f"{out.name}, {out.stat().st_size / 1e6:.1f} МБ")

    v = ffmpeg.stream(info, "video")
    a = ffmpeg.stream(info, "audio")
    W, H, FPS = s.get("video.width"), s.get("video.height"), s.get("video.fps")
    if not v:
        rep.add("Видеодорожка", "fail", "отсутствует")
        return rep
    res_ok = (v.get("width"), v.get("height")) == (W, H)
    rep.add("Разрешение 9:16", "pass" if res_ok else "fail", f"{v.get('width')}x{v.get('height')} (нужно {W}x{H})")
    fps = ffmpeg.fps_of(v)
    rep.add("Частота кадров", "pass" if abs(fps - FPS) < 0.05 else "fail", f"{fps:.2f} fps")
    rep.add("Кодек видео", "pass" if v.get("codec_name") == "h264" else "fail", str(v.get("codec_name")))
    rep.add("Контейнер", "pass" if "mp4" in info.get("format", {}).get("format_name", "") else "fail",
            info.get("format", {}).get("format_name", ""))
    dur = float(info.get("format", {}).get("duration", 0))
    lo, hi = s.get("video.min_duration", 20), s.get("video.max_duration", 35)
    rep.add("Длительность", "pass" if lo <= dur <= hi else "warn", f"{dur:.2f}с (цель {lo}–{hi}с)")

    if not a:
        rep.add("Аудиодорожка", "fail", "отсутствует")
    else:
        rep.add("Аудиодорожка", "pass" if a.get("codec_name") == "aac" else "fail",
                f"{a.get('codec_name')}, {a.get('sample_rate')} Гц")
        vd = float(v.get("duration") or dur)
        ad = float(a.get("duration") or dur)
        diff = abs(vd - ad)
        rep.add("Синхронность длительности A/V", "pass" if diff <= 0.1 else "fail",
                f"видео {vd:.2f}с / аудио {ad:.2f}с (разница {diff:.3f}с)")

    timeline_path = project.dir("output") / "timeline.json"
    timeline = json.loads(timeline_path.read_text(encoding="utf-8")) if timeline_path.exists() else []

    # Маркировка рекламы: если в сцене есть рекламная интеграция, пометка должна быть заполнена
    try:
        for sc in project.load_script().scenes:
            lab = sc.local.get("ad_label")
            if lab:
                rep.add("Маркировка рекламы", "fail" if "<" in lab else "pass",
                        "заполните рекламодателя и erid (токен ОРД) — без этого публиковать нельзя" if "<" in lab
                        else lab, scene=sc.id)
    except Exception:  # noqa: BLE001
        pass

    # Озвучка внутри сцен совпадает с длительностью сцен
    for it in timeline:
        ap = Path(it["audio"]) if it.get("audio") else project.scene_audio(it["scene"])
        if ap.exists():
            adur = ffmpeg.duration(ap)
            if adur > it["duration"] + 0.05:
                rep.add("Озвучка в рамках сцены", "fail",
                        f"аудио {adur:.2f}с длиннее сцены {it['duration']:.2f}с", scene=it["scene"])

    # Чёрные кадры
    try:
        proc = ffmpeg.run(["ffmpeg", "-i", out, "-vf", "blackdetect=d=0.25:pix_th=0.08", "-an", "-f", "null", "-"],
                          quiet=False)
        blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", proc.stderr)
        if blacks:
            for b0, b1 in blacks:
                rep.add("Чёрные кадры", "fail", f"{float(b0):.2f}–{float(b1):.2f}с", scene=scene_at(timeline, float(b0)))
        else:
            rep.add("Чёрные кадры", "pass", "не обнаружены (порог 0.25с)")
    except ffmpeg.FFmpegError as e:
        rep.add("Чёрные кадры", "warn", f"проверка не выполнена: {e}")

    # Тишина там, где должна быть речь
    if a:
        try:
            proc = ffmpeg.run(["ffmpeg", "-i", out, "-af", "silencedetect=n=-45dB:d=1.2", "-vn", "-f", "null", "-"],
                              quiet=False)
            sil = re.findall(r"silence_start: ([\d.]+)", proc.stderr)
            flagged = 0
            for st in sil:
                sc = scene_at(timeline, float(st))
                if sc and project.scene_audio(sc).exists():
                    rep.add("Пропадание звука", "warn", f"тишина >1.2с с {float(st):.2f}с при наличии озвучки",
                            scene=sc)
                    flagged += 1
            if not flagged:
                rep.add("Пропадание звука", "pass", "длинных пауз в сценах с речью нет")
        except ffmpeg.FFmpegError as e:
            rep.add("Пропадание звука", "warn", f"проверка не выполнена: {e}")

    # Субтитры == утверждённый текст
    srt = project.dir("subtitles") / f"{project.id}.srt"
    script = project.load_script()
    expected = normalize_text(" ".join(sc.subtitle_text for sc in script.scenes if sc.subtitle_text))
    if srt.exists():
        got = normalize_text(read_srt_text(srt))
        if got == expected:
            rep.add("Субтитры = сценарий", "pass", f"{len(got)} слов совпадают")
        else:
            first = next((i for i, (x, y) in enumerate(zip(got, expected)) if x != y), min(len(got), len(expected)))
            rep.add("Субтитры = сценарий", "fail",
                    f"расхождение со слова #{first + 1}: '{' '.join(got[first:first + 4])}' vs "
                    f"'{' '.join(expected[first:first + 4])}'")
    else:
        rep.add("Субтитры = сценарий", "fail", "нет файла субтитров")

    # Сценарий не менялся после сборки
    meta = project.meta()
    if meta.get("assembled_fingerprint") and meta["assembled_fingerprint"] != script.fingerprint():
        rep.add("Актуальность сборки", "fail", "сценарий изменён после монтажа — пересоберите ролик")
    else:
        rep.add("Актуальность сборки", "pass", "ролик собран из текущей версии сценария")

    # Исходники сохранены
    missing = [it["scene"] for it in timeline if it["generator"] not in ("local",) and not project.scene_source(it["scene"])]
    rep.add("Исходники сцен сохранены", "pass" if not missing else "fail",
            "все на месте" if not missing else f"нет: {', '.join(missing)}")

    # Пункты, которые автоматически НЕ проверяются
    for it in timeline:
        if it["generator"] not in ("local",):
            rep.add("Внешность кота / артефакты", "manual",
                    "сверить с референсом: мордочка, окрас, глаза, сложенные уши, лапы", scene=it["scene"])
        if it["type"] == "talking":
            rep.add("Мимика и синхронизация губ", "manual", "проверить вручную", scene=it["scene"])
    rep.add("Произношение и интонации", "manual", "прослушать озвучку")
    rep.add("Читаемость субтитров и монтаж", "manual", "просмотреть на телефоне")
    return rep


def save_report(project: Project, rep: Report) -> Path:
    p = project.dir("output") / "qa_report.json"
    p.write_text(json.dumps({"episode": rep.episode, "passed": rep.passed,
                             "checks": [asdict(c) for c in rep.checks]}, ensure_ascii=False, indent=1), encoding="utf-8")
    (project.dir("output") / "qa_report.txt").write_text(rep.text(), encoding="utf-8")
    return p
