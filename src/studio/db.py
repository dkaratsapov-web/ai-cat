"""SQLite: задания генерации, расходы, журнал событий.

Ключевое правило: запись о платной задаче создаётся ДО обращения к API и обновляется
сразу после получения task_id, чтобы после сбоя задача не была отправлена повторно.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import redact

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    episode TEXT NOT NULL,
    scene_id TEXT,
    provider TEXT NOT NULL,
    kind TEXT NOT NULL,              -- image2video | lipsync | tts | text2video ...
    model TEXT,
    params_json TEXT,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL,            -- planned | submitting | submitted | processing | succeeded | failed | unknown | imported | cancelled
    external_task_id TEXT,
    result_url TEXT,
    result_path TEXT,
    est_cost_usd REAL DEFAULT 0,
    actual_cost_usd REAL,
    attempt INTEGER DEFAULT 1,
    paid INTEGER DEFAULT 1,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_key ON jobs(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_jobs_episode ON jobs(episode, scene_id);

CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    episode TEXT,
    provider TEXT,
    amount_usd REAL NOT NULL,
    kind TEXT NOT NULL,              -- estimated | actual | refund
    note TEXT,
    ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode TEXT,
    action TEXT NOT NULL,
    detail TEXT,
    ts TEXT NOT NULL
);
"""

ACTIVE_STATUSES = ("submitting", "submitted", "processing", "unknown")
DONE_STATUSES = ("succeeded", "imported")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---------- jobs ----------
    def create_job(self, *, episode: str, scene_id: str | None, provider: str, kind: str,
                   model: str | None, params: dict, idempotency_key: str, est_cost_usd: float,
                   attempt: int = 1, paid: bool = True, status: str = "planned") -> str:
        job_id = uuid.uuid4().hex
        ts = now_iso()
        with self.conn() as c:
            c.execute(
                "INSERT INTO jobs (id, episode, scene_id, provider, kind, model, params_json, idempotency_key,"
                " status, est_cost_usd, attempt, paid, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, episode, scene_id, provider, kind, model, json.dumps(params, ensure_ascii=False),
                 idempotency_key, status, est_cost_usd, attempt, int(paid), ts, ts),
            )
        return job_id

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        if fields.get("error"):
            fields["error"] = redact(str(fields["error"]))
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.conn() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))

    def get_job(self, job_id: str) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(r) if r else None

    def find_by_key(self, key: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM jobs WHERE idempotency_key = ? ORDER BY created_at", (key,)).fetchall()
        return [dict(r) for r in rows]

    def jobs_for(self, episode: str, scene_id: str | None = None, kind: str | None = None) -> list[dict]:
        q = "SELECT * FROM jobs WHERE episode = ?"
        args: list[Any] = [episode]
        if scene_id:
            q += " AND scene_id = ?"
            args.append(scene_id)
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        q += " ORDER BY created_at"
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    def paid_attempts(self, episode: str, scene_id: str, kind: str) -> int:
        with self.conn() as c:
            r = c.execute(
                "SELECT COUNT(*) FROM jobs WHERE episode=? AND scene_id=? AND kind=? AND paid=1"
                " AND status NOT IN ('planned','cancelled')",
                (episode, scene_id, kind),
            ).fetchone()
        return int(r[0])

    def active_jobs(self, episode: str | None = None) -> list[dict]:
        q = f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})"
        args: list[Any] = list(ACTIVE_STATUSES)
        if episode:
            q += " AND episode = ?"
            args.append(episode)
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    # ---------- costs ----------
    def add_cost(self, *, amount_usd: float, kind: str, provider: str, episode: str | None = None,
                 job_id: str | None = None, note: str = "") -> None:
        with self.conn() as c:
            c.execute(
                "INSERT INTO costs (job_id, episode, provider, amount_usd, kind, note, ts) VALUES (?,?,?,?,?,?,?)",
                (job_id, episode, provider, amount_usd, kind, note, now_iso()),
            )

    def spend(self, *, month: str | None = None, episode: str | None = None) -> float:
        """Сумма расходов: фактические, а там где фактических нет — оценочные."""
        q = (
            "SELECT j.id, j.est_cost_usd, j.actual_cost_usd FROM jobs j"
            " WHERE j.paid = 1 AND j.status NOT IN ('planned','cancelled')"
        )
        args: list[Any] = []
        if month:
            q += " AND substr(j.created_at, 1, 7) = ?"
            args.append(month)
        if episode:
            q += " AND j.episode = ?"
            args.append(episode)
        total = 0.0
        with self.conn() as c:
            for r in c.execute(q, args).fetchall():
                total += r["actual_cost_usd"] if r["actual_cost_usd"] is not None else (r["est_cost_usd"] or 0)
        return round(total, 4)

    def cost_rows(self, episode: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT * FROM costs"
        args: list[Any] = []
        if episode:
            q += " WHERE episode = ?"
            args.append(episode)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    # ---------- events ----------
    def log(self, action: str, detail: Any = "", episode: str | None = None) -> None:
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False)
        detail = redact(detail)
        with self.conn() as c:
            c.execute("INSERT INTO events (episode, action, detail, ts) VALUES (?,?,?,?)",
                      (episode, action, detail, now_iso()))

    def events(self, episode: str | None = None, limit: int = 50) -> list[dict]:
        q = "SELECT * FROM events"
        args: list[Any] = []
        if episode:
            q += " WHERE episode = ?"
            args.append(episode)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]
