"""Связь с Claude без установки Claude Code: чат эпизода ходит через Git-репозиторий.

Владелец → Claude: `studio chat-share <эпизод>` (или кнопка в чате) — коммит и push папки
projects/<эпизод>/director/ (переписка, статусы, ревью директора, заметки) и сценария.
Claude → владелец: Claude пишет ответы в projects/<эпизод>/director/claude_replies.jsonl и пушит;
`git pull` (делается при каждой отправке и кнопкой «Получить ответы») — и они появляются в чате.
Каждый пишет только в свой файл, поэтому конфликтов слияния нет. Ключи и .env в Git не попадают.
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from ..project import Project

SHARED = ["chat.jsonl", "director_notes.md", "latest_review.md", "final_review.md", "status.json", "brief.yaml",
          "lock.json"]


def replies_path(project: Project) -> Path:
    return project.path / "director" / "claude_replies.jsonl"


def _git(root: Path, *args: str, timeout: int = 120) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"{type(e).__name__}: {e}"
    return r.returncode, (r.stdout + r.stderr).strip()


def _branch(root: Path) -> str:
    _c, out = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return out.strip() or "HEAD"


def pull(project: Project) -> tuple[bool, str]:
    """Забрать ответы Claude (и обновления кода)."""
    root = project.settings.root
    code, out = _git(root, "pull", "--no-rebase", "--no-edit", "origin", _branch(root))
    return code == 0, out[-600:]


def share(project: Project) -> tuple[bool, str]:
    """Отправить переписку эпизода Claude через репозиторий. (ok, сообщение)."""
    root = project.settings.root
    ok, msg = pull(project)
    if not ok:
        return False, f"git pull не прошёл: {msg}"
    d = project.path / "director"
    paths = [str((d / f).relative_to(root)) for f in SHARED if (d / f).exists()]
    if project.script_path.exists():
        paths.append(str(project.script_path.relative_to(root)))
    if not paths:
        return False, "Нечего отправлять: в чате эпизода ещё нет сообщений"
    code, out = _git(root, "add", "--", *paths)
    if code:
        return False, f"git add: {out[-400:]}"
    code, out = _git(root, "diff", "--cached", "--quiet")
    if code == 0:
        return True, "Новых сообщений нет — всё уже у Claude"
    code, out = _git(root, "commit", "-m", f"chat: {project.id} {datetime.now():%Y-%m-%d %H:%M}", "--", *paths)
    if code:
        return False, f"git commit: {out[-400:]}"
    code, out = _git(root, "push", "origin", f"HEAD:{_branch(root)}", timeout=180)
    if code:
        return False, f"git push не прошёл: {out[-400:]}"
    return True, "Отправлено Claude. Напишите ему в приложении: «посмотри чат»"
