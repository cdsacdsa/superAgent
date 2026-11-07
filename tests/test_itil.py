from datetime import timedelta

from superagent.config import AgentPolicy
from superagent.itil import finalize_triage, may_auto_resolve, priority_from, redact, sla_status, sla_targets
from superagent.models import Case, CaseStatus, Outcome, OutcomeStatus, Ticket, TicketType, Triage, utcnow


def test_priority_matrix():
    assert priority_from(1, 1) == 1
    assert priority_from(1, 3) == 3
    assert priority_from(2, 2) == 3
    assert priority_from(3, 3) == 4
    assert priority_from(None, 9) == 4  # clamped / defaulted


def test_major_incident_forces_p1_and_no_downgrade():
    t = Ticket(id="X", title="t", priority=2)
    tri = finalize_triage(Triage(impact=3, urgency=3, major_incident=True), t)
    assert tri.priority == 1
    tri = finalize_triage(Triage(impact=3, urgency=3), t)
    assert tri.priority == 2  # service desk said P2, never silently downgrade


def test_sla_targets_and_status():
    p = AgentPolicy()
    opened = utcnow() - timedelta(minutes=200)
    resp, res = sla_targets(p, 1, opened)
    assert res - opened == timedelta(minutes=240)
    c = Case(id="X", ticket=Ticket(id="X", title="t", opened_at=opened), status=CaseStatus.INVESTIGATING,
             sla_resolution_due=res)  # fmt: skip
    assert sla_status(c, p) == "at_risk"
    c.sla_resolution_due = opened + timedelta(minutes=100)
    assert sla_status(c, p) == "breached"
    c.status = CaseStatus.WAITING_REQUESTER  # clock paused
    assert sla_status(c, p) == "ok"


def _case(priority=4, **tri):
    c = Case(id="X", ticket=Ticket(id="X", title="t"))
    c.triage = Triage(priority=priority, ticket_type=TicketType.INCIDENT, **tri)
    return c


def test_auto_resolve_policy():
    p = AgentPolicy()
    o = Outcome(status=OutcomeStatus.RESOLVED, confidence=0.95, customer_message="x")
    ok, why = may_auto_resolve(_case(), o, p)
    assert not ok and "disabled" in why  # default is shadow mode
    p.resolution.auto_resolve = True
    assert may_auto_resolve(_case(), o, p)[0]
    assert not may_auto_resolve(_case(priority=2), o, p)[0]
    assert not may_auto_resolve(_case(security_related=True), o, p)[0]
    o.confidence = 0.8
    assert not may_auto_resolve(_case(), o, p)[0]


def test_redaction():
    s = redact("password: hunter2 and token=abcd1234 Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop")
    assert "hunter2" not in s and "abcd1234" not in s and "eyJhbGci" not in s
    assert "AccountKey=[REDACTED]" in redact("DefaultEndpointsProtocol=https;AccountKey=abc123==;")
