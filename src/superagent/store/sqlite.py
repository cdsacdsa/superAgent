"""SQLite store for local development (single file, survives restarts)."""

from __future__ import annotations

import asyncio
import sqlite3

from ..models import Case, CaseEvent
from .base import CaseStore, ConflictError


class SqliteStore(CaseStore):
    def __init__(self, path: str = "superagent.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = asyncio.Lock()
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, status TEXT, etag TEXT, updated_at TEXT, doc TEXT);
            CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, case_id TEXT, at TEXT, doc TEXT);
            CREATE INDEX IF NOT EXISTS ix_events_case ON events(case_id, at);
            """
        )

    async def get(self, case_id):
        row = self.db.execute("SELECT doc FROM cases WHERE id=?", (case_id,)).fetchone()
        return Case.model_validate_json(row[0]) if row else None

    async def _write(self, case, expected_etag):
        async with self.lock:
            row = self.db.execute("SELECT etag FROM cases WHERE id=?", (case.id,)).fetchone()
            if row is not None and row[0] != expected_etag:
                raise ConflictError(case.id)
            if row is None and expected_etag is not None:
                raise ConflictError(case.id)
            self.db.execute(
                "INSERT OR REPLACE INTO cases (id, status, etag, updated_at, doc) VALUES (?,?,?,?,?)",
                (case.id, case.status.value, case.etag, case.updated_at.isoformat(), case.model_dump_json()),
            )
            self.db.commit()
        return case

    async def list(self, status=None, limit=200):
        if status:
            qs = ",".join("?" * len(status))
            rows = self.db.execute(
                f"SELECT doc FROM cases WHERE status IN ({qs}) ORDER BY updated_at DESC LIMIT ?",
                [s.value for s in status] + [limit],
            ).fetchall()
        else:
            rows = self.db.execute("SELECT doc FROM cases ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [Case.model_validate_json(r[0]) for r in rows]

    async def add_event(self, event):
        async with self.lock:
            self.db.execute(
                "INSERT INTO events (id, case_id, at, doc) VALUES (?,?,?,?)",
                (event.id, event.case_id, event.at.isoformat(), event.model_dump_json()),
            )
            self.db.commit()

    async def events(self, case_id, limit=500):
        rows = self.db.execute(
            "SELECT doc FROM events WHERE case_id=? ORDER BY at ASC LIMIT ?", (case_id, limit)
        ).fetchall()
        return [CaseEvent.model_validate_json(r[0]) for r in rows]

    async def aclose(self):
        self.db.close()
