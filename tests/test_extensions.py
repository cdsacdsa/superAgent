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
