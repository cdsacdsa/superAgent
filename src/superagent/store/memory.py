from __future__ import annotations

from ..models import Case, CaseEvent
from .base import CaseStore, ConflictError


class MemoryStore(CaseStore):
    def __init__(self):
        self.cases: dict[str, str] = {}
        self._events: dict[str, list[CaseEvent]] = {}

    async def get(self, case_id):
        raw = self.cases.get(case_id)
        return Case.model_validate_json(raw) if raw else None

    async def _write(self, case, expected_etag):
        cur = self.cases.get(case.id)
        if cur is not None:
            if Case.model_validate_json(cur).etag != expected_etag:
                raise ConflictError(case.id)
        elif expected_etag is not None:
            raise ConflictError(case.id)
        self.cases[case.id] = case.model_dump_json()
        return case

    async def list(self, status=None, limit=200):
        out = [Case.model_validate_json(v) for v in self.cases.values()]
        if status:
            out = [c for c in out if c.status in status]
        return sorted(out, key=lambda c: c.updated_at, reverse=True)[:limit]

    async def add_event(self, event):
        self._events.setdefault(event.case_id, []).append(event)

    async def events(self, case_id, limit=500):
        return self._events.get(case_id, [])[-limit:]
