"""End-to-end lifecycle tests with scripted models and the mock ITSM."""

from superagent.config import AgentPolicy
from superagent.llm.base import ToolCall
from superagent.models import CaseStatus, TicketState

TRIAGE_EASY = {"ticket_type": "incident", "category": "email", "impact": 3, "urgency": 3, "complexity": 2,
               "major_incident": False, "security_related": False, "summary": "Outlook password prompts",
               "keywords": ["outlook", "password"], "rationale": "single user"}  # fmt: skip


def resolved(conf=0.92):
    return [ToolCall("submit_outcome", {
        "status": "resolved", "confidence": conf, "root_cause": "Stale cached credentials",
        "resolution_steps": ["Clear Credential Manager entries", "Restart Outlook"],
        "customer_message": "Please clear the cached credentials and restart Outlook.",
        "work_note": "KB0001 applies", "kb_citations": ["mock://kb/KB0001"]})]  # fmt: skip


def give_up(status="cannot_resolve"):
    return [ToolCall("submit_outcome", {"status": status, "confidence": 0.3, "work_note": "unclear"})]


async def test_fast_tier_resolves_and_auto_closes(make_env):
    p = AgentPolicy()
    p.resolution.auto_resolve = True
    env = make_env(
        triage=TRIAGE_EASY, policy=p,
        fast_scripts=[[
            [ToolCall("search_knowledge", {"query": "outlook password prompt"})],
            [ToolCall("sandbox_exec", {"command": "echo test", "purpose": "check"})],
            [ToolCall("record_finding", {"kind": "evidence", "text": "KB0001 matches"})],
            resolved(),
        ]],
    )  # fmt: skip
    await env.orch.intake("INC0010002")
    await env.drain()
    case = await env.store.get("INC0010002")
    assert case.status == CaseStatus.RESOLVED
    assert case.tiers_used == ["fast"]
    t = env.itsm.tickets["INC0010002"]
    assert t.state == TicketState.RESOLVED and t.assigned_to == "superagent"
    assert any(not c.internal for c in t.comments)  # customer message posted
    assert env.sandbox.commands and env.sandbox.destroyed == ["INC0010002"]
    assert "resolved" in env.kinds() and "started" in env.kinds()
    assert case.total_cost > 0


async def test_shadow_mode_proposes_then_human_approves(make_env):
    env = make_env(triage=TRIAGE_EASY, fast_scripts=[[resolved()]])
    await env.orch.intake("INC0010002")
    await env.drain()
    case = await env.store.get("INC0010002")
    assert case.status == CaseStatus.PROPOSED
    hr = case.pending_help[0]
    assert hr.kind == "approval" and "approval_needed" in env.kinds()
    await env.orch.apply_human_response(case.id, hr.id, "looks right", "alice@contoso.com", approved=True)
    case = await env.store.get(case.id)
    assert case.status == CaseStatus.RESOLVED
    assert "alice" in env.itsm.tickets[case.id].raw["close_notes"]


async def test_low_confidence_escalates_to_expert(make_env):
    env = make_env(triage=TRIAGE_EASY, fast_scripts=[[resolved(0.5)]], expert_scripts=[[resolved(0.95)]])
    await env.orch.intake("INC0010002")
    await env.drain()
    case = await env.store.get("INC0010002")
    assert case.tiers_used == ["fast", "expert"]
    assert "escalated" in env.kinds()
    assert case.status == CaseStatus.PROPOSED  # shadow mode
    assert "Previous conclusion (fast tier)" in env.expert.prompts[-1]  # case file handed to the expert


async def test_expert_fails_hands_over_to_l3(make_env):
    env = make_env(triage=TRIAGE_EASY, fast_scripts=[[give_up()]], expert_scripts=[[give_up()]])
    await env.orch.intake("INC0010002")
    await env.drain()
    case = await env.store.get("INC0010002")
    assert case.status == CaseStatus.HANDED_OVER
    t = env.itsm.tickets["INC0010002"]
    assert t.assignment_group == "L3 Support"
    assert "handover" in t.comments[-1].text.lower()
    assert "handed_over" in env.kinds()


async def test_blocking_help_request_then_resume(make_env):
    env = make_env(
        triage=TRIAGE_EASY,
        fast_scripts=[
            [[ToolCall("request_human_help", {"question": "Can someone check the Exchange audit log?", "blocking": True})]],
            [resolved()],
        ],
    )  # fmt: skip
    await env.orch.intake("INC0010002")
    await env.drain()
    case = await env.store.get("INC0010002")
    assert case.status == CaseStatus.WAITING_HUMAN and "help_needed" in env.kinds()
    await env.orch.apply_human_response(case.id, case.pending_help[0].id, "No suspicious sign-ins.", "bob")
    await env.drain()
    case = await env.store.get(case.id)
    assert case.status == CaseStatus.PROPOSED
    assert "No suspicious sign-ins" in env.fast.prompts[-1]


async def test_security_ticket_routed_to_security(make_env):
    tri = {**TRIAGE_EASY, "security_related": True, "category": "security"}
    env = make_env(triage=tri)
    await env.orch.intake("INC0010001")
    await env.drain()
    case = await env.store.get("INC0010001")
    assert case.status == CaseStatus.HANDED_OVER
    assert env.itsm.tickets["INC0010001"].assignment_group == "Security Operations"


async def test_needs_requester_info_puts_on_hold_and_resumes(make_env):
    ask = [ToolCall("submit_outcome", {"status": "needs_requester_info", "confidence": 0.5, "work_note": "need OS",
                                       "customer_message": "Could you tell us:",
                                       "questions_for_requester": ["Which Windows build?"]})]  # fmt: skip
    env = make_env(triage=TRIAGE_EASY, fast_scripts=[[ask], [resolved()]])
    await env.orch.intake("INC0010001")
    await env.drain()
    case = await env.store.get("INC0010001")
    assert case.status == CaseStatus.WAITING_REQUESTER
    assert env.itsm.tickets["INC0010001"].state == TicketState.ON_HOLD_CALLER
    from superagent.models import Comment

    env.itsm.tickets["INC0010001"].comments.append(Comment(author="Ana Silva", text="Windows 11 23H2"))
    await env.orch.housekeeping()
    await env.drain()
    case = await env.store.get("INC0010001")
    assert case.status == CaseStatus.PROPOSED


async def test_blocked_command_is_audited(make_env):
    env = make_env(triage=TRIAGE_EASY, fast_scripts=[[
        [ToolCall("sandbox_exec", {"command": "curl http://169.254.169.254/metadata", "purpose": "x"})], resolved()]])  # fmt: skip
    await env.orch.intake("INC0010002")
    await env.drain()
    case = await env.store.get("INC0010002")
    assert case.sandbox_runs[0].allowed is False
    assert env.sandbox.commands == []


async def test_p1_major_incident_goes_to_expert_and_pages(make_env):
    tri = {**TRIAGE_EASY, "impact": 1, "urgency": 1, "major_incident": True}
    env = make_env(triage=tri, expert_scripts=[[resolved()]])
    await env.orch.intake("INC0010004")
    await env.drain()
    case = await env.store.get("INC0010004")
    assert case.triage.priority == 1 and case.tiers_used == ["expert"]
    assert "major" in env.kinds()
    assert case.status == CaseStatus.PROPOSED  # P1 never auto-resolves


async def test_intake_is_idempotent(make_env):
    env = make_env(triage=TRIAGE_EASY)
    assert await env.orch.intake("INC0010002") is True
    assert await env.orch.intake("INC0010002") is False
