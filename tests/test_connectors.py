"""Connector + provider tests against mocked HTTP (no network)."""

import json

import httpx
import httpx2

from superagent.chat.base import Notification
from superagent.chat.teams import TeamsChannel, adaptive_card
from superagent.config import PriceEntry
from superagent.itsm.servicenow import ServiceNowConnector
from superagent.knowledge.rag_api import RagApiSource
from superagent.knowledge.sharepoint import SharePointSource
from superagent.llm.base import ToolCall, ToolResult, ToolSpec
from superagent.models import TicketState
from superagent.sandbox.aca_sessions import AcaSessionsSandbox

SN_REC = {
    "sys_id": {"value": "abc123", "display_value": "abc123"},
    "number": {"value": "INC0001", "display_value": "INC0001"},
    "short_description": {"value": "VPN down", "display_value": "VPN down"},
    "description": {"value": "desc", "display_value": "desc"},
    "state": {"value": "1", "display_value": "New"},
    "priority": {"value": "3", "display_value": "3 - Moderate"},
    "impact": {"value": "2", "display_value": "2 - Medium"},
    "urgency": {"value": "2", "display_value": "2 - Medium"},
    "caller_id": {"value": "u1", "display_value": "Ana Silva"},
    "assignment_group": {"value": "g1", "display_value": "SuperAgent"},
    "opened_at": {"value": "2026-11-01 10:00:00", "display_value": "x"},
}


async def test_servicenow_mapping_and_updates():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, dict(req.url.params), req.content))
        if req.url.path.endswith("/sys_journal_field"):
            return httpx.Response(200, json={"result": [{"element": "work_notes", "value": "note", "sys_created_by": "x",
                                                        "sys_created_on": "2026-11-01 10:05:00"}]})  # fmt: skip
        if req.url.path.endswith("/sys_user_group"):
            return httpx.Response(200, json={"result": [{"sys_id": "grp-l3"}]})
        if req.method == "PATCH":
            return httpx.Response(200, json={"result": {}})
        return httpx.Response(200, json={"result": [SN_REC]})

    sn = ServiceNowConnector("contoso.service-now.com", "u", "p", transport=httpx.MockTransport(handler))
    t = await sn.get_ticket("INC0001")
    assert t.system_id == "abc123" and t.requester == "Ana Silva" and t.priority == 3
    assert t.comments[0].internal and t.state == TicketState.NEW
    await sn.set_state(t, TicketState.ON_HOLD_CALLER, "waiting")
    body = json.loads(calls[-1][3])
    assert body == {"state": "3", "hold_reason": "1", "work_notes": "waiting"}
    await sn.resolve(t, "Solved (Permanently)", "notes")
    assert json.loads(calls[-1][3])["state"] == "6"
    await sn.reassign(t, "L3 Support", "handover")
    assert json.loads(calls[-1][3])["assignment_group"] == "grp-l3"
    q = await sn.list_queue(["SuperAgent"])
    assert q[0].id == "INC0001"
    assert "assignment_group.nameINSuperAgent" in calls[-1][2]["sysparm_query"]


async def test_rag_api_source():
    def handler(req):
        assert json.loads(req.content) == {"query": 'vpn "drops"', "top_k": 3}
        return httpx.Response(200, json={"results": [{"title": "T", "content": "C", "url": "u", "score": 0.5}]})

    src = RagApiSource("https://rag.local/search", "k", transport=httpx.MockTransport(handler))
    hits = await src.search('vpn "drops"', 3)
    assert hits[0].title == "T" and hits[0].source == "rag"


async def test_sharepoint_source():
    class Tok:
        async def token(self):
            return "t"

    def handler(req):
        body = json.loads(req.content)
        assert body["requests"][0]["region"] == "EMEA"
        return httpx.Response(200, json={"value": [{"hitsContainers": [{"hits": [
            {"summary": "<c0>VPN</c0> runbook", "resource": {"name": "VPN.docx", "webUrl": "https://sp/vpn"}}]}]}]})  # fmt: skip

    src = SharePointSource(token_provider=Tok(), transport=httpx.MockTransport(handler))
    hits = await src.search("vpn", 5)
    assert hits[0].content == "VPN runbook" and hits[0].url == "https://sp/vpn"


async def test_teams_card():
    sent = []
    ch = TeamsChannel("https://hook", transport=httpx.MockTransport(lambda r: sent.append(json.loads(r.content)) or httpx.Response(202)))
    n = Notification(kind="help_needed", title="Need help", ticket_id="INC1", facts={"P": "P2"}, url="https://c/cases/INC1")
    await ch.send(n)
    card = sent[0]["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and card["actions"][0]["title"] == "Answer"
    assert adaptive_card(n)["body"][0]["text"].startswith("🙋 INC1")


async def test_aca_sessions_python_pool():
    def handler(req):
        assert req.url.params["identifier"] == "inc0001"
        assert req.headers["authorization"] == "Bearer tok"
        code = json.loads(req.content)["properties"]["code"]
        assert "subprocess.run" in code
        out = json.dumps({"exit_code": 0, "stdout": "hello\n", "stderr": ""})
        return httpx.Response(200, json={"properties": {"status": "Success", "stdout": out + "\n", "stderr": ""}})

    async def tok():
        return "tok"

    sb = AcaSessionsSandbox("https://pool.example", "python", transport=httpx.MockTransport(handler), token_provider=tok)
    r = await sb.exec("INC0001", "echo hello", 30)
    assert r.exit_code == 0 and r.stdout == "hello\n"


# ---------------------------------------------------------------------- providers


async def test_openai_compat_tool_loop():
    from openai import AsyncOpenAI

    from superagent.llm.openai_compat import OpenAICompatProvider

    turns = iter([
        {"tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search_knowledge", "arguments": '{"query":"vpn"}'}}]},
        {"tool_calls": [{"id": "c2", "type": "function", "function": {"name": "submit_outcome",
                         "arguments": '{"status":"resolved","confidence":0.9,"work_note":"x"}'}}]},
    ])  # fmt: skip
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        msg = {"role": "assistant", "content": None, **next(turns)}
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                                         "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls"}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}})  # fmt: skip

    client = AsyncOpenAI(api_key="x", base_url="https://or.local/v1", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    p = OpenAICompatProvider("fast", "m", "https://or.local/v1", "x", PriceEntry(input_per_mtok=1, output_per_mtok=10), client=client)
    executed = []

    async def ex(call: ToolCall):
        executed.append(call.name)
        return ToolResult("ok")

    tools = [ToolSpec("search_knowledge", "d", {"type": "object", "properties": {}}),
             ToolSpec("submit_outcome", "d", {"type": "object", "properties": {}}, terminal=True)]  # fmt: skip
    res = await p.run_agent("sys", "user", tools, ex, 5)
    assert executed == ["search_knowledge", "submit_outcome"]
    assert res.terminal_call.arguments["status"] == "resolved"
    assert seen[1]["messages"][-1] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}
    assert abs(res.usage.cost_usd - 2 * (1000 * 1 + 100 * 10) / 1e6) < 1e-9


async def test_anthropic_provider_request_shape():
    import anthropic

    from superagent.llm.anthropic_provider import AnthropicProvider

    bodies = []
    turns = iter([
        [{"type": "thinking", "thinking": "", "signature": "sig"},
         {"type": "tool_use", "id": "tu1", "name": "search_knowledge", "input": {"query": "x"}}],
        [{"type": "tool_use", "id": "tu2", "name": "submit_outcome", "input": {"status": "cannot_resolve", "confidence": 0.2, "work_note": "n"}}],
    ])  # fmt: skip

    def handler(req):
        bodies.append((req.url.path, dict(req.headers), json.loads(req.content)))
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                          "content": next(turns), "stop_reason": "tool_use", "stop_sequence": None,
                                          "usage": {"input_tokens": 100, "output_tokens": 50}})  # fmt: skip

    client = anthropic.AsyncAnthropic(api_key="x", http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))
    p = AnthropicProvider("expert", "claude-opus-5-5", client, PriceEntry(input_per_mtok=4, output_per_mtok=20),
                          effort="high", server_side_fallback=True)  # fmt: skip

    async def ex(call):
        return ToolResult("result")

    tools = [ToolSpec("search_knowledge", "d", {"type": "object", "properties": {}}),
             ToolSpec("submit_outcome", "d", {"type": "object", "properties": {}}, terminal=True)]  # fmt: skip
    res = await p.run_agent("sys", "user", tools, ex, 5)
    assert res.terminal_call.name == "submit_outcome"
    path, headers, body = bodies[0]
    assert body["model"] == "claude-opus-5-5" and body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "high"} and body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in headers.get("anthropic-beta", "")
    assert "temperature" not in body and "tool_choice" not in body
    # second request: thinking block passed back unchanged, tool results in one user message
    msgs = bodies[1][2]["messages"]
    assert msgs[1]["content"][0]["type"] == "thinking"
    assert msgs[2]["content"][0] == {"type": "tool_result", "tool_use_id": "tu1", "content": "result", "is_error": False}
