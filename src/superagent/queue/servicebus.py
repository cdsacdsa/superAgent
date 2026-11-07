"""Azure Service Bus queue (managed identity: *Azure Service Bus Data Owner* on the namespace)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import AutoLockRenewer, ServiceBusClient

from ..models import QueueMessage
from .base import WorkQueue

log = logging.getLogger(__name__)


class ServiceBusQueue(WorkQueue):
    def __init__(self, namespace: str, queue: str, mi_client_id: str = ""):
        from azure.identity.aio import DefaultAzureCredential

        self.cred = DefaultAzureCredential(managed_identity_client_id=mi_client_id or None)
        self.client = ServiceBusClient(namespace, credential=self.cred)
        self.queue = queue
        self._receiver = None
        self._renewer = AutoLockRenewer(max_lock_renewal_duration=1800)

    async def send(self, msg, delay_s=0):
        async with self.client.get_queue_sender(self.queue) as sender:
            m = ServiceBusMessage(msg.model_dump_json(), message_id=f"{msg.kind}:{msg.ticket_id}:{msg.reason}"[:128],
                                  content_type="application/json")  # fmt: skip
            if delay_s:
                m.scheduled_enqueue_time_utc = datetime.now(UTC) + timedelta(seconds=delay_s)
            await sender.send_messages(m)

    async def receive(self):
        self._receiver = self.client.get_queue_receiver(self.queue, max_wait_time=30)
        async with self._receiver:
            while True:
                batch = await self._receiver.receive_messages(max_message_count=1, max_wait_time=30)
                for m in batch:
                    self._renewer.register(self._receiver, m)
                    try:
                        msg = QueueMessage.model_validate_json(str(m))
                    except Exception:  # noqa: BLE001 - poison message
                        log.error("invalid queue message, dead-lettering")
                        await self._receiver.dead_letter_message(m, reason="invalid payload")
                        continue
                    yield msg, m

    async def complete(self, handle):
        await self._receiver.complete_message(handle)

    async def abandon(self, handle):
        await self._receiver.abandon_message(handle)

    async def aclose(self):
        await self._renewer.close()
        await self.client.close()
        await self.cred.close()
