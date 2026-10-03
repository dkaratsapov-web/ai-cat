"""Тонкая обёртка над FFmpeg / FFprobe."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Sequence


class FFmpegError(RuntimeError):
    pass


def which(name: str) -> str | None:
    return shutil.which(name)


def run(args: Sequence[str | Path], *, quiet: bool = True) -> subprocess.CompletedProcess:
    cmd = [str(a) for a in args]
    if cmd[0] == "ffmpeg":
        cmd[1:1] = ["-hide_banner", "-nostdin", "-y"] + (["-loglevel", "error"] if quiet else [])
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-15:]
        raise FFmpegError(f"{cmd[0]} завершился с ошибкой {proc.returncode}:\n" + "\n".join(tail))
    return proc


def probe(path: Path) -> dict[str, Any]:
    proc = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    return json.loads(proc.stdout or "{}")


def duration(path: Path) -> float:
    info = probe(path)
    d = info.get("format", {}).get("duration")
    if d is None:
        for s in info.get("streams", []):
            if s.get("duration"):
                return float(s["duration"])
        return 0.0
    return float(d)


def stream(info: dict, kind: str) -> dict | None:
    for s in info.get("streams", []):
        if s.get("codec_type") == kind:
            return s
    return None


def fps_of(vstream: dict) -> float:
    rate = vstream.get("avg_frame_rate") or vstream.get("r_frame_rate") or "0/1"
    num, _, den = rate.partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def has_filter(name: str) -> bool:
    """Проверяет фильтр напрямую (`ffmpeg -h filter=NAME`) — не зависит от формата списка в разных версиях FFmpeg."""
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-h", f"filter={name}"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0 and "Unknown filter" not in out and f"Filter {name}" in out


def missing_filters(names) -> list[str]:
    return [n for n in names if not has_filter(n)]


def escape_filter_path(p: Path) -> str:
    """Экранирование пути для аргументов фильтров (subtitles=..., fontsdir=...)."""
    s = str(Path(p).resolve()).replace("\\", "/")
    return s.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")
