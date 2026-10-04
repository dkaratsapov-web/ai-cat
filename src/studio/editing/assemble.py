"""Автоматический монтаж: нормализация сцен → склейка/переходы → озвучка+музыка → субтитры → экспорт.

Повторная сборка никогда не вызывает платные API: используются уже сохранённые исходники сцен.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
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

VOICE_TAIL = 0.2   # пауза после фразы внутри сцены, сек


class AssemblyError(RuntimeError):
    pass


@dataclass
class TimelineItem:
    scene: Scene
    start: float
    duration: float
    audio: Path | None


def build_timeline(project: Project, script: Script) -> list[TimelineItem]:
    t = 0.0
    items = []
    for sc in script.scenes:
        meta = scene_voice_meta(project, sc.id) if sc.voiceover.strip() else None
        if sc.voiceover.strip() and not meta:
            raise AssemblyError(f"{sc.id}: нет озвучки. Выполните: studio voice {project.id}")
        if meta and meta.get("text", "").strip() != sc.voiceover.strip():
            raise AssemblyError(f"{sc.id}: озвучка устарела (текст изменён). Выполните: studio voice {project.id}")
        dur = (meta["duration"] + VOICE_TAIL) if meta else sc.duration
        dur = max(dur, float(sc.local.get("min_duration", 0)))
        items.append(TimelineItem(scene=sc, start=round(t, 3), duration=round(dur, 3),
                                  audio=project.scene_audio(sc.id) if meta else None))
        t += dur
    return items


def normalize_clip(src: Path, out: Path, duration: float, settings: Settings) -> Path:
    """Приводит любой клип к 1080x1920@30, без звука, ровно `duration` секунд (недостающее — стоп-кадр)."""
    w, h, fps = settings.get("video.width"), settings.get("video.height"), settings.get("video.fps")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1,fps={fps},"
          f"tpad=stop_mode=clone:stop_duration={duration:.3f},trim=duration={duration:.3f},setpts=PTS-STARTPTS")
    ffmpeg.run(["ffmpeg", "-i", src, "-vf", vf, "-an", "-c:v", settings.get("video.codec"), "-preset", "veryfast",
                "-crf", str(settings.get("video.crf", 18)), "-pix_fmt", "yuv420p", out])
    return out


def scene_clip(project: Project, item: TimelineItem, length: float, lib: CharacterLibrary) -> Path:
    sc = item.scene
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
    extend = sc.local.get("extend") or ("freeze" if sc.type == "talking" else "pingpong")
    if extend == "pingpong" and ffmpeg.duration(src) < length - 0.05:
        src = pingpong(src, project.dir("work") / f"{sc.id}.pingpong.mp4", project.settings)
    amb = ambient_config(project.settings, sc.local)
    if amb.get("enabled") and sc.type in amb.get("apply_to", []):
        # «Живой слой»: камера, свет, огоньки, пылинки — поверх AI-клипа (бесплатно)
        base = normalize_clip(src, out.with_name(f"{sc.id}.base.mp4"), length, project.settings)
        return apply_ambient(base, out, length, project.settings, amb, seed=sc.id, progress=sc.id)
    return normalize_clip(src, out, length, project.settings)


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


def concat_video(clips: list[Path], lengths: list[float], out: Path, settings: Settings) -> Path:
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
    for i in range(1, len(clips)):
        offset += lengths[i - 1]
        label = f"[v{i}]"
        chain.append(f"{prev}[{i}:v]xfade=transition=fade:duration={d}:offset={offset:.3f}{label}")
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


def assemble(project: Project, *, music: Path | None = None, burn_subtitles: bool = True) -> Path:
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
        print(f"  сцена {it.scene.id}: {it.scene.type}/{it.scene.generator}, {it.duration:.2f}с")
        clips.append(scene_clip(project, it, length, lib))
        lengths.append(it.duration)

    work = project.dir("work")
    print(f"  склейка {len(clips)} сцен с переходами…", flush=True)
    video = concat_video(clips, lengths, work / "video.mp4", settings)
    voice = build_voice_track(items, work / "voice.wav", settings)
    total = round(sum(lengths), 3)

    # Субтитры
    st = settings.get("subtitles")
    cues: list[Cue] = []
    for it in items:
        text = it.scene.subtitle_text
        if text:
            cues += scene_cues(it.scene.id, text, it.start, scene_voice_meta(project, it.scene.id), it.duration,
                               int(st["max_words_per_line"]), int(st["max_chars_per_line"]))
    subs_dir = project.dir("subtitles")
    srt = write_srt(cues, subs_dir / f"{project.id}.srt")
    font_name = ass_font_name(settings.fonts_dir, st["font_name"], st["fallback_font_name"])
    ass = write_ass(cues, subs_dir / f"{project.id}.ass", settings, font_name)
    (subs_dir / "cues.json").write_text(json.dumps([c.__dict__ for c in cues], ensure_ascii=False, indent=1),
                                        encoding="utf-8")

    # Финальный рендер: видео + голос (+ музыка с дакингом) + субтитры + громкость
    music = music or (settings.root / settings.get("music.default_track") if settings.get("music.default_track") else None)
    out = project.final_video
    args: list = ["ffmpeg", "-i", video, "-i", voice]
    lufs = settings.get("video.target_lufs", -14)
    if music and Path(music).exists():
        vol = settings.get("music.volume", 0.12)
        args += ["-stream_loop", "-1", "-i", music]
        afilter = (f"[1:a]asplit=2[vo][sc];[2:a]volume={vol},atrim=duration={total:.3f}[mu];"
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
        "-crf", str(settings.get("video.crf", 18)), "-pix_fmt", settings.get("video.pix_fmt", "yuv420p"),
        "-r", str(settings.get("video.fps")), "-c:a", settings.get("video.audio_codec", "aac"),
        "-b:a", settings.get("video.audio_bitrate", "192k"), "-ar", str(settings.get("video.audio_sample_rate", 48000)),
        "-t", f"{total:.3f}", "-movflags", "+faststart", out,
    ]
    print("  финальный рендер: голос, субтитры, громкость (1–5 минут)…", flush=True)
    ffmpeg.run(args)
    (project.dir("output") / "timeline.json").write_text(json.dumps(
        [{"scene": it.scene.id, "start": it.start, "duration": it.duration, "type": it.scene.type,
          "generator": it.scene.generator} for it in items], ensure_ascii=False, indent=1), encoding="utf-8")
    project.set_status("assembled", assembled_fingerprint=script.fingerprint())
    return out
