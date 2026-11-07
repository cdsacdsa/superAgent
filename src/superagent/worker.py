"""Worker process: ITSM poller + queue consumers + housekeeping (SLA, requester replies, VM reaping, digest).

Run: ``python -m superagent.worker``
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime

from .agent.orchestrator import Orchestrator
from .chat.base import Notification
from .config import get_settings
from .container import build_orchestrator
from .models import CaseStatus
from .observability import setup_observability

log = logging.getLogger("superagent.worker")


class Worker:
    def __init__(self, orch: Orchestrator, concurrency: int = 4, poll: bool = True):
        self.orch = orch
        self.concurrency = concurrency
        self.poll = poll
        self._last_digest: date | None = None
        self._stop = asyncio.Event()

    async def poll_once(self) -> int:
        s = self.orch.s
        n = 0
        tickets = await s.itsm.list_queue(s.policy.itil.assignment_groups)
        for t in tickets:
            if await self.orch.intake(t.id, reason="poll"):
                n += 1
        if n:
            log.info("poller: %d new ticket(s)", n)
        return n

    async def _poller(self):
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001
                log.exception("poller failed")
            await self._sleep(self.orch.policy.poll_interval_seconds)

    async def _consumer(self, idx: int):
        q = self.orch.s.queue
        async for msg, handle in q.receive():
            try:
                await self.orch.handle(msg)
                await q.complete(handle)
            except Exception:  # noqa: BLE001
                log.exception("consumer %d: failed %s", idx, msg.ticket_id)
                await q.abandon(handle)
            if self._stop.is_set():
                return

    async def housekeeping_once(self):
        s = self.orch.s
        await self.orch.housekeeping()
        # expert-tier daily spend across all replicas (from the shared store)
        today = datetime.now(UTC).date()
        cases = await s.store.list(limit=1000)
        spend = sum(c.usage["expert"].cost_usd for c in cases if "expert" in c.usage and c.updated_at.date() == today)
        s.router.set_expert_spend_today(spend)
        vm = s.sandbox.by_target.get("windows") if s.sandbox else None
        if vm is not None and hasattr(vm, "reap_expired"):
            await vm.reap_expired()
        if datetime.now(UTC).hour >= s.policy.itil.digest_hour_utc and self._last_digest != today:
            self._last_digest = today
            await self.send_digest(cases)

    async def send_digest(self, cases):
        today = datetime.now(UTC).date()
        todays = [c for c in cases if c.updated_at.date() == today or c.created_at.date() == today]
        by = lambda st: sum(1 for c in todays if c.status == st)  # noqa: E731
        resolved, handed = by(CaseStatus.RESOLVED), by(CaseStatus.HANDED_OVER)
        done = resolved + handed
        active = sum(1 for c in cases if c.status not in (CaseStatus.RESOLVED, CaseStatus.HANDED_OVER))
        await self.orch.s.chat.safe_send(
            Notification(
                kind="digest", title="Daily support agent digest",
                facts={"Tickets touched": str(len(todays)), "Resolved by agent": str(resolved),
                       "Handed over": str(handed), "Auto-resolution rate": f"{(resolved / done * 100) if done else 0:.0f}%",
                       "Open in agent": str(active), "AI cost today": f"${sum(c.total_cost for c in todays):.2f}"},
                url=self.orch.s.base_url,
            )
        )  # fmt: skip

    async def _housekeeping(self):
        while not self._stop.is_set():
            try:
                await self.housekeeping_once()
            except Exception:  # noqa: BLE001
                log.exception("housekeeping failed")
            await self._sleep(300)

    async def _sleep(self, seconds: float):
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def run(self):
        tasks = [asyncio.create_task(self._consumer(i)) for i in range(self.concurrency)]
        tasks.append(asyncio.create_task(self._housekeeping()))
        if self.poll:
            tasks.append(asyncio.create_task(self._poller()))
        log.info("worker started: %d consumers, poller=%s", self.concurrency, self.poll)
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

    def stop(self):
        self._stop.set()


async def main():
    settings = get_settings()
    setup_observability(settings, "superagent-worker")
    orch = build_orchestrator()
    await Worker(orch, settings.worker_concurrency, settings.poll_enabled).run()


if __name__ == "__main__":
    asyncio.run(main())
