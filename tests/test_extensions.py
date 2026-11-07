"""Foundry auth, Atlassian knowledge, document ingestion, MCP tools, product sandbox (no network)."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from superagent.agent.tools import CaseToolbox, tool_specs
from superagent.config import McpServerConfig, Settings
from superagent.knowledge.atlassian import ConfluenceSource, JiraKnowledgeSource, _terms
from superagent.knowledge.ingest import chunk_markdown, iter_documents
from superagent.llm.base import ToolCall
from superagent.mcp_tools import McpToolBridge

ROOT = Path(__file__).resolve().parents[1]


def _completion(content: str) -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}  # fmt: skip


async def test_fast_tier_on_foundry_uses_managed_identity_token(monkeypatch):
    from superagent.config import AgentPolicy
    from superagent.container import build_llm
    from superagent.llm import openai_compat

    async def token() -> str:
        return "entra-token"

    monkeypatch.setattr(openai_compat, "azure_token_provider", lambda client_id="": token)
    s = Settings(fast_use_entra=True, fast_base_url="https://res.services.ai.azure.com/openai/v1/", fast_model="fast-model")
    p = build_llm("fast", "openai_compat", "fast-model", s, AgentPolicy())
    seen = []

    def handler(req: httpx.Request):
        seen.append((str(req.url), req.headers.get("authorization")))
        return httpx.Response(200, json=_completion('{"ok": true}'))

    p.client = p.client.with_options(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out, _ = await p.complete_json("sys", "user", {"type": "object"}, "x")
    assert out == {"ok": True}
    assert seen == [("https://res.services.ai.azure.com/openai/v1/chat/completions", "Bearer entra-token")]


def test_query_terms_strip_query_operators():
    assert _terms('error "ERR-EXPORT-500" (csv) OR export*') == "error ERR EXPORT 500 csv OR export"


async def test_confluence_search_scoped_to_spaces():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(path=req.url.path, cql=req.url.params["cql"], auth=req.headers["authorization"])
        return httpx.Response(200, json={"_links": {"base": "https://contoso.atlassian.net/wiki"}, "results": [
            {"title": "CSV export runbook", "space": {"name": "IT Support"},
             "body": {"view": {"value": "<p>Use <b>?encoding=utf-8</b></p><script>x()</script>"}},
             "_links": {"webui": "/spaces/IT/pages/1"}}]})  # fmt: skip

    src = ConfluenceSource("https://contoso.atlassian.net", "bot@contoso.com", "tok", "IT, PROD",
                           transport=httpx.MockTransport(handler))  # fmt: skip
    hits = await src.search('csv "export"', 3)
    assert seen["path"] == "/wiki/rest/api/content/search"
    assert seen["cql"] == 'type = page AND text ~ "csv export" AND space in ("IT", "PROD")'
    assert seen["auth"].startswith("Basic ")
    assert hits[0].title == "CSV export runbook (IT Support)"
    assert hits[0].content == "Use ?encoding=utf-8"
    assert hits[0].url == "https://contoso.atlassian.net/wiki/spaces/IT/pages/1"


async def test_jira_knowledge_includes_resolution_and_comments():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(json.loads(req.content))
        adf = lambda t: {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]}]}  # noqa: E731
        return httpx.Response(200, json={"issues": [{"key": "ORD-17", "fields": {
            "summary": "Export fails for accented names", "description": adf("500 on export"),
            "status": {"name": "Done"}, "resolution": {"name": "Fixed"}, "issuetype": {"name": "Bug"},
            "comment": {"comments": [{"author": {"displayName": "Dev"}, "body": adf("fixed in 2.4.1")}]}}}]})  # fmt: skip

    src = JiraKnowledgeSource("https://contoso.atlassian.net", "u", "t", "ORD", transport=httpx.MockTransport(handler))
    hits = await src.search("export 500", 5)
    assert seen["jql"] == 'text ~ "export 500" AND project in ("ORD") ORDER BY updated DESC'
    assert hits[0].title == "ORD-17: Export fails for accented names"
    assert "Resolution: Fixed" in hits[0].content and "Comment (Dev): fixed in 2.4.1" in hits[0].content
    assert hits[0].url == "https://contoso.atlassian.net/browse/ORD-17"


def test_chunk_markdown_splits_at_headings_and_long_sections():
    text = "# Title\nintro\n\n## A\n" + "\n\n".join(["x" * 900] * 3) + "\n## B\nshort"
    chunks = chunk_markdown(text, max_chars=2000)
    assert [h for h, _ in chunks] == ["Title", "A", "A", "B"]
    assert all(len(b) <= 2000 for _, b in chunks)


def test_sample_product_docs_ingest():
    docs = list(iter_documents(ROOT / "samples" / "knowledge", source="docs"))
    assert len({d["id"] for d in docs}) == len(docs)
    ki = [d for d in docs if "KI-2041" in d["title"]]
    assert ki and "encoding=utf-8" in ki[0]["content"]
    assert ki[0]["url"] == "contoso-orders/known-issues.md" and ki[0]["category"] == "contoso-orders"


def _product_server():
    from mcp.server.mcpserver import MCPServer

    srv = MCPServer("product")

    @srv.tool()
    def get_order(order_id: str) -> str:
        """Look up an order."""
        return f"order {order_id}: shipped"

    @srv.tool()
    def delete_order(order_id: str) -> str:
        """Delete an order."""
        return "deleted"

    return srv


async def test_mcp_bridge_offers_only_allowed_tools_and_audits_calls(make_env):
    from mcp import Client

    srv = _product_server()
    bridge = McpToolBridge([McpServerConfig(name="product", url="unused", allowed_tools=["get_order", "missing"])],
                           connect=lambda cfg: Client(srv))  # fmt: skip
    specs = await bridge.tool_specs()
    assert [s.name for s in specs] == ["mcp_product__get_order"]
    assert specs[0].parameters["properties"]["order_id"]["type"] == "string"

    env = make_env()
    env.services.mcp = bridge
    case = await env.orch.s.store.save(_case(env))
    box = CaseToolbox(env.services, case, "fast")
    res = await box(ToolCall("mcp_product__get_order", {"order_id": "1003"}))
    assert not res.is_error and "order 1003: shipped" in res.content and "<untrusted_output>" in res.content
    denied = await box(ToolCall("mcp_product__delete_order", {"order_id": "1003"}))
    assert denied.is_error and "unknown tool" in denied.content


async def test_mcp_bridge_survives_unreachable_server():
    def broken(cfg):
        raise ConnectionError("down")

    bridge = McpToolBridge([McpServerConfig(name="x", url="http://nowhere", allowed_tools=["a"])], connect=broken)
    assert await bridge.tool_specs() == []


def _case(env):
    from superagent.models import Case

    t = env.itsm.tickets["INC0010005"]
    return Case(id=t.id, ticket=t)


def test_product_sandbox_target_and_notes():
    from superagent.config import AgentPolicy
    from superagent.container import build_sandbox

    sb = build_sandbox(Settings(sandbox_provider="mock"), AgentPolicy())
    assert set(sb.targets) == {"linux", "product", "windows"}
    spec = next(s for s in tool_specs(["linux", "product"], ["docs"], {"product": "runs at localhost:3000"})
                if s.name == "sandbox_exec")  # fmt: skip
    assert "product (runs at localhost:3000)" in spec.description
    assert spec.parameters["properties"]["target"]["enum"] == ["linux", "product"]


def test_demo_product_bug_and_workaround(monkeypatch, tmp_path):
    monkeypatch.setenv("PRODUCT_LOG_DIR", str(tmp_path))
    monkeypatch.delenv("EXPORT_ENCODING", raising=False)
    spec = importlib.util.spec_from_file_location("demo_app", ROOT / "sandbox" / "product" / "demo_app" / "app.py")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    with pytest.raises(UnicodeEncodeError):
        app.export_csv(app.ORDERS)
    assert "João Gonçalves" in app.export_csv(app.ORDERS, "utf-8").decode("utf-8")


def test_vm_setup_script_gets_installer_link():
    from superagent.sandbox.azure_vm import AzureVmSandbox

    vm = AzureVmSandbox.__new__(AzureVmSandbox)
    vm.setup_script = 'Invoke-WebRequest -Uri "{installer_url}" -OutFile x.msi'
    vm.installer_blob_url = "https://sbx.blob.core.windows.net/installers/product.msi"
    vm._installer_sas_url = lambda: vm.installer_blob_url + "?sig=abc"
    assert vm.render_setup_script() == ('Invoke-WebRequest -Uri "https://sbx.blob.core.windows.net/installers/'
                                        'product.msi?sig=abc" -OutFile x.msi')  # fmt: skip


async def test_reasoning_models_get_max_completion_tokens_and_effort():
    from openai import AsyncOpenAI

    from superagent.llm.openai_compat import OpenAICompatProvider

    bodies = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        bodies.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens' is not supported "
                                                       "with this model. Use 'max_completion_tokens' instead.",
                                                       "type": "invalid_request_error", "param": "max_tokens"}})  # fmt: skip
        return httpx.Response(200, json=_completion('{"a": 1}'))

    client = AsyncOpenAI(api_key="x", base_url="https://res.services.ai.azure.com/openai/v1/", max_retries=0,
                         http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))  # fmt: skip
    p = OpenAICompatProvider("fast", "gpt-5.4-mini", "", "x", None, client=client, reasoning_effort="low")
    assert (await p.complete_json("s", "u", {}, "x"))[0] == {"a": 1}
    assert (await p.complete_json("s", "u", {}, "x"))[0] == {"a": 1}
    assert [("max_tokens" in b, b.get("max_completion_tokens"), b.get("reasoning_effort")) for b in bodies] == [
        (True, None, "low"), (False, 4000, "low"), (False, 4000, "low")]  # the switch is remembered


def test_secret_placeholders_from_bicep_are_treated_as_empty():
    s = Settings(fast_api_key="not-set", teams_webhook_url="not-set", fast_use_entra=True)
    assert s.fast_api_key == "" and s.teams_webhook_url == ""


async def test_failing_case_is_handed_over_after_three_attempts(make_env):
    from superagent.models import CaseStatus

    env = make_env()

    async def broken(*args, **kwargs):
        raise RuntimeError("model unavailable")

    env.fast.complete_json = broken
    assert await env.orch.intake("INC0010002")
    msg = env.queue.q.get_nowait()
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await env.orch.handle(msg)
    await env.orch.handle(msg)  # third failure: handed over instead of redelivered
    case = await env.store.get("INC0010002")
    assert case.attempts == 3 and case.status == CaseStatus.HANDED_OVER


async def test_memory_queue_redelivers_failed_messages_after_a_delay():
    import asyncio

    from superagent.models import QueueMessage
    from superagent.queue.base import MemoryQueue

    q = MemoryQueue(retry_delay_s=0.05)
    await q.abandon(QueueMessage(kind="process_ticket", ticket_id="X"))
    assert q.q.empty()
    await asyncio.sleep(0.1)
    assert q.q.get_nowait().ticket_id == "X"


async def test_file_product_is_started_in_the_code_interpreter_session():
    from superagent.sandbox.aca_sessions import AcaSessionsSandbox

    codes = []

    def handler(req: httpx.Request):
        codes.append(json.loads(req.content)["properties"]["code"])
        out = json.dumps({"exit_code": 0, "stdout": "500", "stderr": ""})
        return httpx.Response(200, json={"properties": {"status": "Success", "stdout": out + "\n", "stderr": ""}})

    async def tok():
        return "tok"

    sb = AcaSessionsSandbox("https://pool.example", "python", transport=httpx.MockTransport(handler), token_provider=tok,
                            bootstrap_files={"app.py": "print('hi')"}, start_cmd="python3 app.py")  # fmt: skip
    res = await sb.exec("INC0010005", "curl -s -o /dev/null -w %{http_code} localhost:3000/export.csv", 30)
    assert res.stdout == "500"
    code = codes[0]
    assert code.index("if not _sa_up():") < code.index("subprocess.run")  # product checked/started first
    assert "'python3 app.py'" in code and "connect(('127.0.0.1', 3000))" in code
    compile(code, "session", "exec")  # the generated code is valid Python


def test_product_target_uses_code_interpreter_when_no_product_pool():
    from superagent.config import AgentPolicy
    from superagent.container import FILE_PRODUCT_NOTES, build_sandbox, product_notes

    s = Settings(sandbox_provider="aca_sessions", aca_pool_endpoint="https://pool.example",
                 product_bootstrap_dir=str(ROOT / "sandbox" / "product" / "demo_app"))  # fmt: skip
    sb = build_sandbox(s, AgentPolicy())
    assert set(sb.targets) == {"linux", "product"}
    assert "app.py" in sb.by_target["product"].bootstrap or "YXBw" in sb.by_target["product"].bootstrap
    assert product_notes(s) == FILE_PRODUCT_NOTES


async def test_requeue_only_open_cases(make_env):
    from superagent.models import CaseStatus

    env = make_env()
    assert await env.orch.intake("INC0010002")
    env.queue.q.get_nowait()  # the message is lost (e.g. the in-memory queue restarted)
    assert await env.orch.requeue("INC0010002") and env.queue.q.get_nowait().ticket_id == "INC0010002"
    case = await env.store.get("INC0010002")
    case.status = CaseStatus.PROPOSED
    await env.store.save(case)
    assert not await env.orch.requeue("INC0010002")  # waiting for approval: left alone
    assert not await env.orch.requeue("NOPE")


async def test_sandbox_commands_bypass_the_session_proxy_for_localhost():
    from superagent.sandbox.aca_sessions import AcaSessionsSandbox

    codes = []

    def handler(req: httpx.Request):
        codes.append(json.loads(req.content)["properties"]["code"])
        out = json.dumps({"exit_code": 0, "stdout": "", "stderr": ""})
        return httpx.Response(200, json={"properties": {"status": "Success", "stdout": out + "\n", "stderr": ""}})

    async def tok():
        return "tok"

    sb = AcaSessionsSandbox("https://pool.example", "python", transport=httpx.MockTransport(handler), token_provider=tok)
    await sb.exec("X", "curl -s localhost:3000/health", 30)
    ns: dict = {}
    exec(codes[0].split("r = subprocess.run")[0], ns)  # run only the env preparation
    assert ns["env"]["no_proxy"].startswith("localhost,127.0.0.1") and "env=env" in codes[0]


async def test_message_for_a_leased_case_is_retried_not_dropped(make_env):
    env = make_env()
    assert await env.orch.intake("INC0010002")
    msg = env.queue.q.get_nowait()
    assert await env.store.acquire("INC0010002", "a-worker-that-died")
    sent = []

    async def record(m, delay_s=0):
        sent.append((m.ticket_id, delay_s))

    env.queue.send = record
    await env.orch.handle(msg)
    assert sent == [("INC0010002", 60)]
