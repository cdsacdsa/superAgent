"""Case store: cases (agent state per ticket) + append-only events (audit trail)."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from ..models import Case, CaseEvent, CaseStatus, utcnow


class ConflictError(Exception):
    """Optimistic concurrency conflict (someone else updated the case)."""


class CaseStore(ABC):
    @abstractmethod
    async def get(self, case_id: str) -> Case | None: ...

    @abstractmethod
    async def _write(self, case: Case, expected_etag: str | None) -> Case:
        """Insert or replace. If ``expected_etag`` is set it must match the stored etag."""

    @abstractmethod
    async def list(self, status: list[CaseStatus] | None = None, limit: int = 200) -> list[Case]: ...

    @abstractmethod
    async def add_event(self, event: CaseEvent) -> None: ...

    @abstractmethod
    async def events(self, case_id: str, limit: int = 500) -> list[CaseEvent]: ...

    async def save(self, case: Case) -> Case:
        case.updated_at = utcnow()
        expected = case.etag
        case.etag = uuid.uuid4().hex
        try:
            return await self._write(case, expected)
        except ConflictError:
            case.etag = expected
            raise

    async def create_if_absent(self, case: Case) -> bool:
        if await self.get(case.id):
            return False
        case.etag = None
        try:
            await self.save(case)
            return True
        except ConflictError:
            return False

    async def acquire(self, case_id: str, owner: str, ttl_s: int = 900) -> Case | None:
        """Lease a case for exclusive processing. Returns the case or None if busy."""
        case = await self.get(case_id)
        if case is None:
            return None
        now = utcnow()
        if case.lease_owner and case.lease_owner != owner and case.lease_until and case.lease_until > now:
            return None
        case.lease_owner = owner
        case.lease_until = now + timedelta(seconds=ttl_s)
        try:
            return await self.save(case)
        except ConflictError:
            return None

    async def release(self, case: Case) -> Case:
        case.lease_owner = ""
        case.lease_until = None
        return await self.save(case)

    async def aclose(self) -> None:
        return None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None
