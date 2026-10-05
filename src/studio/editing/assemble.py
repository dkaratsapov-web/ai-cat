"""Автоматический монтаж: нормализация сцен → склейка/переходы → озвучка+музыка → субтитры → экспорт.

Повторная сборка никогда не вызывает платные API: используются уже сохранённые исходники сцен.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..character.library import CharacterLibrary, CharacterError
from ..config import Settings
from ..generation.voice import scene_voice_meta
from ..models import Scene, Script
from ..project import Project
from . import ffmpeg
from .fonts import ass_font_name
from .ambient import ambient_config, apply_ambient
from .local_scenes import render_local_scene
from .subtitles import Cue, scene_cues, write_ass, write_srt

VOICE_TAIL = 0.2   # пауза после фразы внутри сцены, сек (video.voice_tail в конфиге)


class AssemblyError(RuntimeError):
    pass


@dataclass
class TimelineItem:
    scene: Scene
    start: float
    duration: float
    audio: Path | None
    meta: dict | None = field(default=None)   # озвучка сцены (после сокращения пауз): duration, words


def build_timeline(project: Project, script: Script) -> list[TimelineItem]:
    t = 0.0
    items = []
    tail = float(project.settings.get("video.voice_tail", VOICE_TAIL))
    for sc in script.scenes:
        meta = scene_voice_meta(project, sc.id) if sc.voiceover.strip() else None
        if sc.voiceover.strip() and not meta:
            raise AssemblyError(f"{sc.id}: нет озвучки. Выполните: studio voice {project.id}")
        if meta and meta.get("text", "").strip() != sc.voiceover.strip():
            raise AssemblyError(f"{sc.id}: озвучка устарела (текст изменён). Выполните: studio voice {project.id}")
        audio = project.scene_audio(sc.id) if meta else None
        if meta and audio:
            audio, meta = tighten_voice(project, sc, audio, meta)
        dur = (meta["duration"] + tail) if meta else sc.duration
        dur = max(dur, float(sc.local.get("min_duration", 0)))
        items.append(TimelineItem(scene=sc, start=round(t, 3), duration=round(dur, 3), audio=audio, meta=meta))
        t += dur
    return items


# ---------------------------------------------------------------- озвучка: лишние паузы

def tighten_voice(project: Project, scene: Scene, audio: Path, meta: dict) -> tuple[Path, dict]:
    """Убирает тишину в начале/конце фразы и укорачивает длинные паузы внутри — запись та же, темп тот же.

    Говорящие сцены не трогаем: губы аватара синхронизированы с исходным файлом.
    Время слов пересчитывается, поэтому субтитры и подсветки остаются синхронными."""
    cfg = {"enabled": True, "noise_db": -42, "min_silence": 0.12, "lead": 0.05, "tail": 0.1, "max_pause": 0.3,
           **(project.settings.get("video.voice_tighten") or {})}
    if not cfg["enabled"] or scene.type == "talking" or scene.local.get("tighten") is False:
        return audio, meta
    params = json.dumps({k: cfg[k] for k in sorted(cfg)}, sort_keys=True) + str(meta.get("key")) + str(audio.stat().st_size)
    tag = hashlib.sha256(params.encode()).hexdigest()[:12]
    work = project.dir("work") / "voice"
    work.mkdir(parents=True, exist_ok=True)
    out, out_meta = work / f"{scene.id}.tight.wav", work / f"{scene.id}.tight.json"
    if out.exists() and out_meta.exists():
        cached = json.loads(out_meta.read_text(encoding="utf-8"))
        if cached.get("tag") == tag:
            return out, cached["meta"]
    dur = float(meta.get("duration") or ffmpeg.duration(audio))
    proc = ffmpeg.run(["ffmpeg", "-i", audio, "-af",
                       f"silencedetect=noise={cfg['noise_db']}dB:d={cfg['min_silence']}", "-f", "null", "-"], quiet=False)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr or "")]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr or "")]
    cuts: list[tuple[float, float]] = []
    for i, st in enumerate(starts):
        st = max(0.0, st)
        en = ends[i] if i < len(ends) else dur
        if st <= 0.02:
            cuts.append((0.0, max(0.0, en - cfg["lead"])))
        elif en >= dur - 0.02:
            cuts.append((min(dur, st + cfg["tail"]), dur))
        elif en - st > cfg["max_pause"]:
            half = cfg["max_pause"] / 2
            cuts.append((st + half, en - half))
    # Защита: никогда не резать внутри слов (тайминги слов от синтеза речи) и не трогать «сплошную тишину»
    spans = [(float(a) - 0.03, float(b) + 0.03) for _w, a, b in meta.get("words") or []]
    safe: list[tuple[float, float]] = []
    for a, b in cuts:
        pieces = [(a, b)]
        for sa, sb in spans:
            nxt = []
            for pa, pb in pieces:
                if sb <= pa or sa >= pb:
                    nxt.append((pa, pb))
                    continue
                if sa > pa:
                    nxt.append((pa, sa))
                if sb < pb:
                    nxt.append((sb, pb))
            pieces = nxt
        safe += pieces
    cuts = [(a, b) for a, b in safe if b - a > 0.02]
    removed = sum(b - a for a, b in cuts)
    if removed < 0.05 or removed > 0.4 * dur:
        return audio, meta
    keep, pos = [], 0.0
    for a, b in sorted(cuts):
        if a > pos:
            keep.append((pos, a))
        pos = max(pos, b)
    if pos < dur:
        keep.append((pos, dur))

    def remap(t: float) -> float:
        acc = 0.0
        for a, b in keep:
            if t <= a:
                return acc
            if t <= b:
                return acc + (t - a)
            acc += b - a
        return acc

    sr = project.settings.get("video.audio_sample_rate", 48000)
    parts = []
    for i, (a, b) in enumerate(keep):
        ln = b - a
        fade = min(0.01, ln / 4)
        parts.append(f"[0:a]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={fade:.3f},afade=t=out:st={max(0.0, ln - fade):.4f}:d={fade:.3f}[k{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[k{i}]" for i in range(len(keep))) + f"concat=n={len(keep)}:v=0:a=1[out]"
    ffmpeg.run(["ffmpeg", "-i", audio, "-filter_complex", graph, "-map", "[out]", "-ar", str(sr), "-c:a", "pcm_s16le", out])
    new = dict(meta)
    new["duration"] = round(sum(b - a for a, b in keep), 3)
    new["words"] = [[w, round(remap(float(a)), 3), round(remap(float(b)), 3)] for w, a, b in meta.get("words") or []]
    out_meta.write_text(json.dumps({"tag": tag, "meta": new}, ensure_ascii=False), encoding="utf-8")
    return out, new


# ---------------------------------------------------------------- время по словам озвучки

def _norm(word: str) -> str:
    return re.sub(r"[^\wё]", "", str(word).lower().replace("ё", "е"))


def anchor_time(value, words: list, default: float = 0.5) -> float:
    """Число — секунды от начала сцены; строка — момент начала слова озвучки («слово» или «слово#2»)."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    token, _, nth = str(value).partition("#")
    want, n = _norm(token), int(nth or 1)
    for w, a, _b in words or []:
        if want and _norm(w).startswith(want):
            n -= 1
            if n == 0:
                return max(0.0, float(a) - 0.05)
    print(f"    ! слово «{value}» не найдено в озвучке — беру {default:.1f} с")
    return default


def resolve_local(scene: Scene, meta: dict | None) -> Scene:
    """Подставляет время слов в local: highlights[].at/until, bullet_at, overlays[].at/until."""
    words = (meta or {}).get("words") or []
    loc = dict(scene.local)
    if loc.get("highlights"):
        loc["highlights"] = [{**hl, "at": anchor_time(hl.get("at"), words),
                              **({"until": anchor_time(hl["until"], words)} if hl.get("until") is not None else {})}
                             for hl in loc["highlights"]]
    if loc.get("bullet_at"):
        loc["bullet_at"] = [anchor_time(x, words, 0.3 + i) for i, x in enumerate(loc["bullet_at"])]
    if loc.get("overlays"):
        loc["overlays"] = [{**o, "at": anchor_time(o.get("at", 0.0), words, 0.0),
                            **({"until": anchor_time(o["until"], words)} if o.get("until") is not None else {}),
                            **({"items": [{**it, "at": anchor_time(it.get("at", 0.0), words, 0.0)}
                                          for it in o["items"]]} if o.get("items") else {})}
                           for o in loc["overlays"]]
    return Scene(**{**scene.__dict__, "local": loc})


def normalize_clip(src: Path, out: Path, duration: float, settings: Settings) -> Path:
    """Приводит любой клип к 1080x1920@30, без звука, ровно `duration` секунд (недостающее — стоп-кадр)."""
    w, h, fps = settings.get("video.width"), settings.get("video.height"), settings.get("video.fps")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1,fps={fps},"
          f"tpad=stop_mode=clone:stop_duration={duration:.3f},trim=duration={duration:.3f},setpts=PTS-STARTPTS")
    ffmpeg.run(["ffmpeg", "-i", src, "-vf", vf, "-an", "-c:v", settings.get("video.codec"), "-preset", "veryfast",
                "-crf", str(settings.get("video.crf", 18)), "-pix_fmt", "yuv420p", out])
    return out


def scene_clip(project: Project, item: TimelineItem, length: float, lib: CharacterLibrary) -> Path:
    sc = resolve_local(item.scene, item.meta)
    out = project.scene_clip(sc.id)
    if sc.generator == "local":
        char_img = None
        if sc.local.get("kind") == "character":
            try:
                _ref, char_img = lib.resolve(sc.reference)
            except CharacterError as e:
                raise AssemblyError(str(e)) from e
        return render_local_scene(sc, project.path, project.settings, out, character_image=char_img, duration=length)
    src = project.scene_source(sc.id)
    if not src:
        hint = (f"studio scene import {project.id} {sc.id} <файл.mp4>" if sc.generator == "manual"
                else f"studio generate {project.id} --scenes {sc.id}")
        raise AssemblyError(f"{sc.id}: нет исходника сцены ({sc.generator}). Выполните: {hint}")
    # Короткий клип без речи продлеваем «туда-обратно», а не стоп-кадром (говорящие — только стоп-кадр,
    # иначе губы разойдутся с речью)
    # Короткий клип: по умолчанию лёгкое замедление (до ×1,3 — на глаз незаметно), остаток — стоп-кадр,
    # который «оживляет» наезд камеры живого слоя. «Туда-обратно» (pingpong) — только по явному запросу:
    # повтор движения заметен. Говорящие сцены не замедляем — губы разойдутся с речью.
    extend = sc.local.get("extend") or ("freeze" if sc.type == "talking" else "slow")
    src_dur = ffmpeg.duration(src)
    if extend == "pingpong" and src_dur < length - 0.05:
        src = pingpong(src, project.dir("work") / f"{sc.id}.pingpong.mp4", project.settings)
    elif extend == "slow" and src_dur < length - 0.05:
        factor = min(length / max(src_dur, 0.1), float(sc.local.get("max_slow", 1.3)))
        if factor > 1.01:
            src = slow_clip(src, project.dir("work") / f"{sc.id}.slow.mp4", factor, project.settings)
    amb = ambient_config(project.settings, sc.local)
    live = bool(amb.get("enabled")) and sc.type in amb.get("apply_to", [])
    overlays = list(sc.local.get("overlays") or [])
    if sc.local.get("ad_label"):   # маркировка рекламы (закон «О рекламе», ст. 18.1) — на весь показ сцены
        overlays.append({"text": sc.local["ad_label"], "style": "label"})
        if "<" in sc.local["ad_label"]:
            print(f"    ! {sc.id}: маркировка рекламы не заполнена (рекламодатель, erid) — публиковать нельзя")
    if live or overlays:
        # «Живой слой» (камера, свет, огоньки, пылинки) и надписи — поверх AI-клипа (бесплатно)
        base = normalize_clip(src, out.with_name(f"{sc.id}.base.mp4"), length, project.settings)
        return apply_ambient(base, out, length, project.settings, {**amb, "enabled": live}, seed=sc.id,
                             progress=sc.id, overlays=overlays)
    return normalize_clip(src, out, length, project.settings)


def slow_clip(src: Path, out: Path, factor: float, settings: Settings) -> Path:
    """Плавное замедление клипа в factor раз (без повтора движения)."""
    fps = settings.get("video.fps")
    ffmpeg.run(["ffmpeg", "-i", src, "-vf", f"setpts={factor:.4f}*PTS,fps={fps}", "-an", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", str(settings.get("video.crf", 18)), "-pix_fmt", "yuv420p", out])
    return out


def pingpong(src: Path, out: Path, settings: Settings, repeats: int = 3) -> Path:
    """Клип → клип + реверс (×repeats): плавное продление коротких анимаций без повторной генерации."""
    parts = "".join(f"[f{i}][r{i}]" for i in range(repeats))
    split = f"[0:v]split={repeats * 2}" + "".join(f"[f{i}]" for i in range(repeats)) + \
            "".join(f"[s{i}]" for i in range(repeats))
    revs = ";".join(f"[s{i}]reverse[r{i}]" for i in range(repeats))
    graph = f"{split};{revs};{parts}concat=n={repeats * 2}:v=1:a=0[v]"
    ffmpeg.run(["ffmpeg", "-i", src, "-filter_complex", graph, "-map", "[v]", "-an", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", out])
    return out


def _xfade_names() -> set[str]:
    try:
        proc = ffmpeg.run(["ffmpeg", "-h", "filter=xfade"])
        return set(re.findall(r"^\s{4,}(\w+)\s+-?\d+\s", proc.stdout or "", re.M))
    except Exception:  # noqa: BLE001
        return {"fade"}


def transition_name(prev: Scene, nxt: Scene, settings: Settings, available: set[str]) -> str:
    """Переход по смыслу: кот → скрин «пролёт», скрин → скрин «свайп», к карточке — сдвиг вверх.

    Своё значение для сцены: local.transition (переход НА эту сцену)."""
    rules = {"default": "fade", "video_to_local": "zoomin", "local_to_local": "smoothleft",
             "to_card": "smoothup", "to_video": "fade", **(settings.get("video.transitions") or {})}
    name = nxt.local.get("transition")
    if name == "cut":   # чистая склейка без наплыва (например, вход в CTA)
        return "cut"
    if not name:
        prev_local, next_local = prev.generator == "local", nxt.generator == "local"
        if not next_local:
            name = rules["to_video"]
        elif nxt.local.get("kind") == "card":
            name = rules["to_card"]
        elif prev_local:
            name = rules["local_to_local"]
        else:
            name = rules["video_to_local"]
    return name if name in available else "fade"


def concat_video(clips: list[Path], lengths: list[float], out: Path, settings: Settings,
                 scenes: list[Scene] | None = None) -> Path:
    mode = settings.get("video.transition", "cut")
    d = float(settings.get("video.transition_duration", 0.25))
    if mode != "xfade" or len(clips) < 2:
        lst = out.with_suffix(".txt")
        lst.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
        ffmpeg.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
        return out
    # Каждый клип (кроме последнего) длиннее на d; смещение перехода = накопленная длительность сцен,
    # поэтому сцены остаются синхронны с озвучкой.
    args: list = ["ffmpeg"]
    for c in clips:
        args += ["-i", c]
    chain = []
    prev = "[0:v]"
    offset = 0.0
    available = _xfade_names() if scenes else {"fade"}
    for i in range(1, len(clips)):
        offset += lengths[i - 1]
        label = f"[v{i}]"
        kind = transition_name(scenes[i - 1], scenes[i], settings, available) if scenes else "fade"
        dur = d
        if kind == "cut":   # склейка: наплыв в 1 кадр, смещение то же — синхрон с озвучкой сохраняется
            kind, dur = "fade", 0.034
        chain.append(f"{prev}[{i}:v]xfade=transition={kind}:duration={dur}:offset={offset:.3f}{label}")
        prev = label
    args += ["-filter_complex", ";".join(chain), "-map", prev, "-c:v", settings.get("video.codec"),
             "-preset", "veryfast", "-crf", str(settings.get("video.crf", 18)), "-pix_fmt", "yuv420p", out]
    ffmpeg.run(args)
    return out


def build_voice_track(items: list[TimelineItem], out: Path, settings: Settings) -> Path:
    sr = settings.get("video.audio_sample_rate", 48000)
    args: list = ["ffmpeg"]
    filters = []
    n = 0
    for it in items:
        if it.audio:
            args += ["-i", it.audio]
            filters.append(f"[{n}:a]aformat=sample_rates={sr}:channel_layouts=mono,apad,atrim=duration={it.duration:.3f}[a{n}]")
        else:
            args += ["-f", "lavfi", "-t", f"{it.duration:.3f}", "-i", f"anullsrc=r={sr}:cl=mono"]
            filters.append(f"[{n}:a]atrim=duration={it.duration:.3f}[a{n}]")
        n += 1
    filters.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]")
    args += ["-filter_complex", ";".join(filters), "-map", "[out]", "-c:a", "pcm_s16le", out]
    ffmpeg.run(args)
    return out


def assemble(project: Project, *, music: Path | None = None, burn_subtitles: bool = True,
             use_music: bool = True, suffix: str = "") -> Path:
    settings = project.settings
    script = project.load_script()
    if project.status in ("draft", "cancelled") or not project.is_script_approved():
        raise AssemblyError(f"Сценарий не утверждён или изменён после утверждения: studio script approve {project.id}")
    lib = CharacterLibrary(settings)
    items = build_timeline(project, script)
    # Сначала проверяем, что все AI-сцены скачаны, — чтобы не ждать монтаж впустую
    missing = [it.scene.id for it in items if it.scene.generator != "local" and not project.scene_source(it.scene.id)]
    if missing:
        raise AssemblyError(f"Ещё нет видео сцен: {', '.join(missing)}. Дождитесь генерации: "
                            f"studio status {project.id} --refresh (повторяйте, пока все не станут succeeded)")
    use_xfade = settings.get("video.transition") == "xfade" and len(items) > 1
    d = float(settings.get("video.transition_duration", 0.25)) if use_xfade else 0.0

    clips, lengths = [], []
    for i, it in enumerate(items):
        length = it.duration + (d if i < len(items) - 1 else 0.0)
        src = project.scene_source(it.scene.id) if it.scene.generator != "local" else None
        print(f"  сцена {it.scene.id}: {it.scene.type}/{it.scene.generator}, {it.duration:.2f}с"
              + (f" ← {src.name}" if src else ""))
        clips.append(scene_clip(project, it, length, lib))
        lengths.append(it.duration)

    work = project.dir("work")
    print(f"  склейка {len(clips)} сцен с переходами…", flush=True)
    video = concat_video(clips, lengths, work / "video.mp4", settings, scenes=[it.scene for it in items])
    voice = build_voice_track(items, work / "voice.wav", settings)
    total = round(sum(lengths), 3)

    # Субтитры
    st = settings.get("subtitles")
    cues: list[Cue] = []
    for it in items:
        text = it.scene.subtitle_text
        if text:
            cues += scene_cues(it.scene.id, text, it.start, it.meta, it.duration,
                               int(st["max_words_per_line"]), int(st["max_chars_per_line"]))
    subs_dir = project.dir("subtitles")
    srt = write_srt(cues, subs_dir / f"{project.id}.srt")
    font_name = ass_font_name(settings.fonts_dir, st["font_name"], st["fallback_font_name"])
    ass = write_ass(cues, subs_dir / f"{project.id}.ass", settings, font_name)
    (subs_dir / "cues.json").write_text(json.dumps([c.__dict__ for c in cues], ensure_ascii=False, indent=1),
                                        encoding="utf-8")

    # Финальный рендер: видео + голос (+ музыка с дакингом) + субтитры + громкость
    music = music or (settings.root / settings.get("music.default_track") if settings.get("music.default_track") else None)
    if not use_music:
        music = None
    out = project.final_video.with_name(project.final_video.stem + suffix + project.final_video.suffix) if suffix else project.final_video
    args: list = ["ffmpeg", "-i", video, "-i", voice]
    lufs = settings.get("video.target_lufs", -14)
    if music and Path(music).exists():
        vol = settings.get("music.volume", 0.12)
        args += ["-stream_loop", "-1", "-i", music]
        fo = max(0.0, total - 1.5)
        afilter = (f"[1:a]asplit=2[vo][sc];[2:a]volume={vol},atrim=duration={total:.3f},"
                   f"afade=t=in:d=0.6,afade=t=out:st={fo:.3f}:d=1.5[mu];"
                   f"[mu][sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck];"
                   f"[vo][duck]amix=inputs=2:duration=first:normalize=0,loudnorm=I={lufs}:TP=-1.5:LRA=11[aout]")
    else:
        afilter = f"[1:a]loudnorm=I={lufs}:TP=-1.5:LRA=11[aout]"
    vfilter = "[0:v]null[vout]"
    if burn_subtitles and st.get("enabled", True) and cues:
        vfilter = f"[0:v]ass='{ffmpeg.escape_filter_path(ass)}':fontsdir='{ffmpeg.escape_filter_path(settings.fonts_dir)}'[vout]"
    args += [
        "-filter_complex", f"{vfilter};{afilter}", "-map", "[vout]", "-map", "[aout]",
        "-c:v", settings.get("video.codec"), "-preset", settings.get("video.preset", "medium"),
        *(["-b:v", settings.get("video.bitrate"), "-maxrate", settings.get("video.maxrate", settings.get("video.bitrate")),
           "-bufsize", settings.get("video.bufsize", "30M")] if settings.get("video.bitrate")
          else ["-crf", str(settings.get("video.crf", 18))]),
        "-pix_fmt", settings.get("video.pix_fmt", "yuv420p"),
        "-r", str(settings.get("video.fps")), "-c:a", settings.get("video.audio_codec", "aac"),
        "-b:a", settings.get("video.audio_bitrate", "192k"), "-ar", str(settings.get("video.audio_sample_rate", 48000)),
        "-t", f"{total:.3f}", "-movflags", "+faststart", out,
    ]
    print("  финальный рендер: голос, субтитры, громкость (1–5 минут)…", flush=True)
    ffmpeg.run(args)
    (project.dir("output") / "timeline.json").write_text(json.dumps(
        [{"scene": it.scene.id, "start": it.start, "duration": it.duration, "type": it.scene.type,
          "generator": it.scene.generator, "audio": str(it.audio) if it.audio else None} for it in items], ensure_ascii=False, indent=1), encoding="utf-8")
    project.set_status("assembled", assembled_fingerprint=script.fingerprint())
    return out
