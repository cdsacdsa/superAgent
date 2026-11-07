import pytest

from superagent.agent.orchestrator import Orchestrator, Services
from superagent.chat.base import ConsoleChannel
from superagent.config import AgentPolicy
from superagent.itsm.mock import MockITSM
from superagent.knowledge.base import KnowledgeHub
from superagent.knowledge.itsm_kb import ItsmKnowledgeSource
from superagent.llm.fake import FakeProvider
from superagent.llm.router import ModelRouter
from superagent.queue.base import MemoryQueue
from superagent.sandbox.base import SandboxRouter
from superagent.sandbox.mock import MockSandbox
from superagent.store.memory import MemoryStore


class Env:
    def __init__(self, fast: FakeProvider, expert: FakeProvider | None, policy: AgentPolicy):
        self.itsm = MockITSM()
        self.sandbox = MockSandbox()
        self.chat = ConsoleChannel()
        self.queue = MemoryQueue()
        self.store = MemoryStore()
        self.fast, self.expert, self.policy = fast, expert, policy
        self.services = Services(
            policy=policy, itsm=self.itsm, knowledge=KnowledgeHub([ItsmKnowledgeSource(self.itsm)]),
            router=ModelRouter(fast, expert, policy.routing), store=self.store, queue=self.queue,
            chat=self.chat, sandbox=SandboxRouter({"linux": self.sandbox}), base_url="http://test",
        )  # fmt: skip
        self.orch = Orchestrator(self.services)

    async def drain(self, max_msgs: int = 20):
        """Process queued messages until the queue is empty."""
        n = 0
        while not self.queue.q.empty() and n < max_msgs:
            msg = self.queue.q.get_nowait()
            await self.orch.handle(msg)
            n += 1
        return n

    def kinds(self):
        return [n.kind for n in self.chat.sent]


@pytest.fixture
def make_env():
    def _make(fast_scripts=None, expert_scripts=None, triage=None, policy: AgentPolicy | None = None, expert=True):
        policy = policy or AgentPolicy()
        fast = FakeProvider("fast", triage=triage, scripts=fast_scripts or [])
        exp = FakeProvider("expert", triage=triage, scripts=expert_scripts or []) if expert else None
        return Env(fast, exp, policy)

    return _make
