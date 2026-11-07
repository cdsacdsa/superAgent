"""Dependency wiring: builds every service from Settings + AgentPolicy."""

from __future__ import annotations

import logging
import os
import socket

from .agent.orchestrator import Orchestrator, Services
from .chat.base import ChatChannel, ConsoleChannel
from .config import AgentPolicy, Settings, get_settings, load_policy
from .itsm.base import ITSMConnector
from .knowledge.base import KnowledgeHub, KnowledgeSource
from .llm.base import LLMProvider
from .llm.router import ModelRouter
from .queue.base import MemoryQueue, WorkQueue
from .sandbox.base import Sandbox, SandboxRouter
from .store.base import CaseStore

log = logging.getLogger(__name__)


def build_llm(tier: str, provider: str, model: str, s: Settings, policy: AgentPolicy) -> LLMProvider:
    price = policy.prices.get(model)
    if provider == "fake":
        from .llm.fake import FakeProvider

        return FakeProvider(tier)
    if provider == "openai_compat":
        from .llm.openai_compat import OpenAICompatProvider

        base_url = s.fast_base_url if tier == "fast" else (s.expert_base_url or s.fast_base_url)
        key = s.fast_api_key if tier == "fast" else (s.expert_api_key or s.fast_api_key)
        return OpenAICompatProvider(tier, model, base_url, key, price)
    from .llm.anthropic_provider import AnthropicProvider, build_anthropic_client

    key = s.expert_api_key if tier == "expert" else s.fast_api_key
    client = build_anthropic_client(provider, api_key=key, base_url=s.expert_base_url if tier == "expert" else "",
                                    foundry_resource=s.foundry_resource, foundry_use_entra=s.foundry_use_entra,
                                    azure_client_id=s.azure_client_id)  # fmt: skip
    return AnthropicProvider(
        tier, model, client, price,
        effort=policy.routing.expert_effort if tier == "expert" else "low",
        server_side_fallback=(provider == "anthropic" and policy.routing.expert_server_side_fallback and not s.expert_base_url),
    )  # fmt: skip


def build_itsm(s: Settings) -> ITSMConnector:
    if s.itsm_provider == "servicenow":
        from .itsm.servicenow import ServiceNowConnector

        return ServiceNowConnector(s.servicenow_instance, s.servicenow_user, s.servicenow_password,
                                   s.servicenow_agent_user_sys_id)  # fmt: skip
    if s.itsm_provider == "jira":
        from .itsm.jira_sm import JiraServiceManagementConnector

        return JiraServiceManagementConnector(s.jira_base_url, s.jira_user, s.jira_api_token, s.jira_project_key,
                                              s.jira_agent_account_id)  # fmt: skip
    from .itsm.mock import MockITSM

    return MockITSM()


def build_knowledge(s: Settings, itsm: ITSMConnector) -> tuple[KnowledgeHub, object | None]:
    from .knowledge.itsm_kb import ItsmKnowledgeSource

    sources: list[KnowledgeSource] = [ItsmKnowledgeSource(itsm)]
    kedb = None
    if s.sharepoint_enabled:
        from .knowledge.sharepoint import SharePointSource

        sources.append(SharePointSource(s.graph_tenant_id, s.graph_client_id, s.graph_client_secret, s.graph_region,
                                        s.sharepoint_site_filter, mi_client_id=s.azure_client_id))  # fmt: skip
    if s.rag_api_url:
        from .knowledge.rag_api import RagApiSource

        sources.append(RagApiSource(s.rag_api_url, s.rag_api_key, s.rag_api_key_header))
    if s.azure_search_endpoint and s.azure_search_index:
        from .knowledge.azure_search import AzureSearchSource

        src = AzureSearchSource(s.azure_search_endpoint, s.azure_search_index, s.azure_search_key, s.azure_client_id)
        sources.append(src)
        kedb = src
    return KnowledgeHub(sources), kedb


def build_sandbox(s: Settings, policy: AgentPolicy) -> SandboxRouter | None:
    by_target: dict[str, Sandbox] = {}
    p = s.sandbox_provider
    if p == "disabled":
        return None
    if p == "mock":
        from .sandbox.mock import MockSandbox

        m = MockSandbox()
        by_target = {"linux": m, "windows": m}
    elif p == "docker":
        from .sandbox.docker_local import DockerSandbox

        by_target["linux"] = DockerSandbox(s.docker_image)
    elif p == "aca_sessions":
        from .sandbox.aca_sessions import AcaSessionsSandbox

        by_target["linux"] = AcaSessionsSandbox(s.aca_pool_endpoint, s.aca_pool_type, s.azure_client_id)
    # The VM sandbox can be combined with any of the above (adds the windows target).
    if s.vm_resource_group and s.vm_subnet_id:
        from .sandbox.azure_vm import AzureVmSandbox

        vm = AzureVmSandbox(s.vm_subscription_id, s.vm_resource_group, s.vm_subnet_id, s.vm_location, s.vm_size,
                            policy.sandbox.vm_ttl_minutes, s.azure_client_id)  # fmt: skip
        by_target["windows"] = vm
        by_target.setdefault("linux", vm)
    return SandboxRouter(by_target) if by_target else None


def build_chat(s: Settings) -> ChatChannel:
    if s.chat_provider == "teams" and s.teams_webhook_url:
        from .chat.teams import TeamsChannel

        return TeamsChannel(s.teams_webhook_url)
    if s.chat_provider == "slack" and s.slack_webhook_url:
        from .chat.slack import SlackChannel

        return SlackChannel(s.slack_webhook_url)
    return ConsoleChannel()


def build_store(s: Settings) -> CaseStore:
    if s.store_provider == "cosmos":
        from .store.cosmos import CosmosStore

        return CosmosStore(s.cosmos_endpoint, s.cosmos_database, s.azure_client_id)
    if s.store_provider == "memory":
        from .store.memory import MemoryStore

        return MemoryStore()
    from .store.sqlite import SqliteStore

    return SqliteStore(s.sqlite_path)


def build_queue(s: Settings) -> WorkQueue:
    if s.queue_provider == "servicebus":
        from .queue.servicebus import ServiceBusQueue

        return ServiceBusQueue(s.servicebus_namespace, s.servicebus_queue, s.azure_client_id)
    return MemoryQueue()


def build_services(settings: Settings | None = None, policy: AgentPolicy | None = None, **overrides) -> Services:
    s = settings or get_settings()
    policy = policy or load_policy(s.policy_file)
    itsm = overrides.pop("itsm", None) or build_itsm(s)
    knowledge, kedb = (overrides.pop("knowledge"), None) if "knowledge" in overrides else build_knowledge(s, itsm)
    fast = overrides.pop("fast", None) or build_llm("fast", s.fast_provider, s.fast_model, s, policy)
    expert = overrides.pop("expert", None)
    if expert is None and policy.routing.expert_enabled:
        expert = build_llm("expert", s.expert_provider, s.expert_model, s, policy)
    services = Services(
        policy=policy,
        itsm=itsm,
        knowledge=knowledge,
        router=ModelRouter(fast, expert, policy.routing),
        store=overrides.pop("store", None) or build_store(s),
        queue=overrides.pop("queue", None) or build_queue(s),
        chat=overrides.pop("chat", None) or build_chat(s),
        sandbox=overrides.pop("sandbox") if "sandbox" in overrides else build_sandbox(s, policy),
        base_url=s.public_base_url.rstrip("/"),
        worker_id=f"{socket.gethostname()}-{os.getpid()}",
        kedb_writer=kedb,
    )
    return services


def build_orchestrator(**kw) -> Orchestrator:
    return Orchestrator(build_services(**kw))
