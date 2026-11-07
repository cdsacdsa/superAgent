"""Work queue. Memory for local dev; Azure Service Bus in production (KEDA scales the worker on it)."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from ..models import QueueMessage


class WorkQueue(ABC):
    @abstractmethod
    async def send(self, msg: QueueMessage, delay_s: int = 0) -> None: ...

    @abstractmethod
    def receive(self) -> AsyncIterator[tuple[QueueMessage, object]]:
        """Yield (message, handle). Call ``complete(handle)`` or ``abandon(handle)``."""

    async def complete(self, handle: object) -> None:
        return None

    async def abandon(self, handle: object) -> None:
        return None

    async def aclose(self) -> None:
        return None


class MemoryQueue(WorkQueue):
    def __init__(self, retry_delay_s: float = 30):
        self.q: asyncio.Queue[QueueMessage] = asyncio.Queue()
        self.retry_delay_s = retry_delay_s  # failed messages come back after this, not in a tight loop

    async def send(self, msg, delay_s=0):
        if delay_s:
            asyncio.get_running_loop().call_later(delay_s, self.q.put_nowait, msg)
        else:
            await self.q.put(msg)

    async def receive(self):
        while True:
            msg = await self.q.get()
            yield msg, msg

    async def abandon(self, handle):
        await self.send(handle, delay_s=self.retry_delay_s)  # type: ignore[arg-type]
