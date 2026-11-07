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
    def __init__(self):
        self.q: asyncio.Queue[QueueMessage] = asyncio.Queue()

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
        await self.q.put(handle)  # type: ignore[arg-type]
