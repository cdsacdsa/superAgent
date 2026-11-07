"""Configuration.

Two layers:
* ``Settings`` reads environment variables (secrets, endpoints, provider selection) and is
  populated from Key Vault-backed Container Apps secrets in Azure or a ``.env`` file locally.
* ``AgentPolicy`` reads ``config/agent.yaml`` (thresholds, ITIL policy, prices, prompts).
  This is behaviour you tune without redeploying infrastructure.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SA_", extra="ignore")

    environment: str = "local"
    policy_file: str = str(REPO_ROOT / "config" / "agent.yaml")
    public_base_url: str = "http://localhost:8000"
    console_api_key: str = ""  # local auth for the console; in Azure use Entra ID (Easy Auth)
    webhook_secret: str = ""
    run_worker_inproc: bool = True  # local all-in-one mode; set false in Azure (separate worker app)
    worker_concurrency: int = 4
    poll_enabled: bool = True

    # ---- LLM: fast tier (OpenAI-compatible: OpenRouter / DeepSeek / Azure AI Foundry / fake)
    fast_provider: Literal["openai_compat", "anthropic", "foundry", "fake"] = "openai_compat"
    fast_model: str = "deepseek/deepseek-v4.1-flash"
    fast_base_url: str = "https://openrouter.ai/api/v1"
    fast_api_key: str = ""
    fast_use_entra: bool = False  # Foundry OpenAI-compatible endpoint with the managed identity (no key)
    fast_reasoning_effort: str = ""  # sent as reasoning_effort when set (e.g. "low" for GPT-5-class models)

    # ---- LLM: expert tier (Claude Opus 5.5 via Claude API or Microsoft Foundry)
    expert_provider: Literal["anthropic", "foundry", "openai_compat", "fake"] = "anthropic"
    expert_model: str = "claude-opus-5-5"
    expert_api_key: str = ""  # ANTHROPIC_API_KEY also honoured by the SDK
    expert_base_url: str = ""
    foundry_resource: str = ""  # Microsoft Foundry resource name (Claude in Foundry)
    foundry_use_entra: bool = True  # use managed identity instead of an API key

    # ---- ITSM
    itsm_provider: Literal["servicenow", "jira", "mock"] = "mock"
    servicenow_instance: str = ""  # e.g. contoso.service-now.com
    servicenow_user: str = ""
    servicenow_password: str = ""
    servicenow_agent_user_sys_id: str = ""
    jira_base_url: str = ""  # e.g. https://contoso.atlassian.net
    jira_user: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""
    jira_service_desk_id: str = ""
    jira_agent_account_id: str = ""

    # ---- Knowledge
    sharepoint_enabled: bool = False
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""  # empty => managed identity / DefaultAzureCredential
    graph_region: str = "EMEA"  # required by Graph search for app-only calls
    sharepoint_site_filter: str = ""  # optional KQL e.g. path:"https://contoso.sharepoint.com/sites/IT"
    rag_api_url: str = ""
    rag_api_key: str = ""
    rag_api_key_header: str = "Authorization"
    azure_search_endpoint: str = ""
    azure_search_index: str = ""
    azure_search_key: str = ""  # empty => managed identity
    # Confluence + Jira as read-only knowledge (one Atlassian site, API token of a read-only user)
    atlassian_base_url: str = ""  # e.g. https://contoso.atlassian.net
    atlassian_user: str = ""
    atlassian_api_token: str = ""
    confluence_enabled: bool = False
    confluence_spaces: str = ""  # comma-separated space keys; empty = every space the user can read
    jira_kb_enabled: bool = False
    jira_kb_projects: str = ""  # comma-separated project keys; empty = every project the user can read

    # ---- Sandbox
    sandbox_provider: Literal["aca_sessions", "azure_vm", "docker", "mock", "disabled"] = "mock"
    aca_pool_endpoint: str = ""
    aca_pool_type: Literal["python", "custom"] = "python"
    # "product" target: a custom-container pool (or local Docker image) running the product under test
    aca_product_pool_endpoint: str = ""
    docker_product_image: str = ""
    product_sandbox_notes: str = (
        "the product under test runs inside this sandbox at http://localhost:3000 (logs in /tmp/product/); "
        "use `browse <url> [--step click:<selector>] [--step fill:<selector>=<value>]` to drive its web UI"
    )
    docker_image: str = "superagent-sandbox:latest"
    vm_subscription_id: str = ""
    vm_resource_group: str = ""
    vm_subnet_id: str = ""
    vm_location: str = "westeurope"
    vm_size: str = "Standard_B2s"
    vm_admin_password: str = ""
    vm_setup_script: str = ""  # file run once on every new sandbox VM (e.g. installs the product)
    vm_installer_blob_url: str = ""  # optional; a 2-hour read-only SAS for it replaces {installer_url} in the script

    # ---- Chat
    chat_provider: Literal["teams", "slack", "console"] = "console"
    teams_webhook_url: str = ""
    slack_webhook_url: str = ""

    # ---- Store / queue
    store_provider: Literal["sqlite", "memory", "cosmos"] = "sqlite"
    sqlite_path: str = "superagent.db"
    cosmos_endpoint: str = ""
    cosmos_database: str = "superagent"
    queue_provider: Literal["memory", "servicebus"] = "memory"
    servicebus_namespace: str = ""  # <name>.servicebus.windows.net
    servicebus_queue: str = "tickets"

    # ---- Observability
    applicationinsights_connection_string: str = Field(
        default_factory=lambda: os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    )
    azure_client_id: str = Field(default_factory=lambda: os.environ.get("AZURE_CLIENT_ID", ""))

    @model_validator(mode="after")
    def _drop_secret_placeholders(self) -> Settings:
        # The Bicep stores "not-set" for empty secrets so Key Vault references always resolve.
        for name, value in self.__dict__.items():
            if value == "not-set":
                setattr(self, name, "")
        return self


# --------------------------------------------------------------------------- policy (YAML)


class PriceEntry(BaseModel):
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0


class RoutingPolicy(BaseModel):
    expert_enabled: bool = True
    expert_direct_complexity: int = 5
    p1_expert_first: bool = True
    min_confidence: float = 0.7
    fast_max_steps: int = 14
    expert_max_steps: int = 20
    expert_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    per_ticket_budget_usd: float = 3.0
    daily_expert_budget_usd: float = 50.0
    expert_server_side_fallback: bool = True


class ResolutionPolicy(BaseModel):
    auto_resolve: bool = False  # start in shadow/assist mode; enable once trusted
    auto_resolve_threshold: float = 0.85
    auto_resolve_max_priority: int = 3  # only P3/P4 (higher number = lower priority)
    auto_resolve_types: list[str] = ["incident", "service_request"]
    auto_resolve_categories: list[str] = []  # empty = any category
    post_customer_updates: bool = True


class ItilPolicy(BaseModel):
    assignment_groups: list[str] = ["SuperAgent"]
    handover_group: str = "L3 Support"
    security_group: str = "Security Operations"
    major_incident_group: str = "Major Incident Management"
    sla_minutes: dict[str, dict[str, int]] = {
        # priority -> response/resolution targets in minutes
        "1": {"response": 15, "resolution": 240},
        "2": {"response": 30, "resolution": 480},
        "3": {"response": 240, "resolution": 1440},
        "4": {"response": 480, "resolution": 4320},
    }
    sla_warning_ratio: float = 0.75
    problem_threshold: int = 3  # similar incidents in window -> propose Problem
    problem_window_hours: int = 72
    auto_create_problem: bool = False  # False = propose in chat/work note only
    requester_timeout_hours: int = 72  # reminder after this, handover after 2x
    digest_hour_utc: int = 7


class SandboxPolicy(BaseModel):
    max_runtime_seconds: int = 120
    max_output_chars: int = 12000
    network_requires_approval: bool = True
    vm_ttl_minutes: int = 120
    extra_deny_patterns: list[str] = []


class McpServerConfig(BaseModel):
    """An MCP server whose tools the agent may use (product APIs, GitHub, Atlassian, ...)."""

    name: str
    url: str  # streamable HTTP endpoint
    description: str = ""
    headers: dict[str, str] = {}  # a value "env:NAME" is read from the environment (Key Vault-backed in Azure)
    allowed_tools: list[str] = []  # only these tools are offered to the model; keep them read-only
    timeout_seconds: int = 60


class AgentPolicy(BaseModel):
    agent_name: str = "SuperAgent"
    poll_interval_seconds: int = 60
    knowledge_top_k: int = 5
    routing: RoutingPolicy = RoutingPolicy()
    resolution: ResolutionPolicy = ResolutionPolicy()
    itil: ItilPolicy = ItilPolicy()
    sandbox: SandboxPolicy = SandboxPolicy()
    mcp_servers: list[McpServerConfig] = []
    prices: dict[str, PriceEntry] = {
        "deepseek/deepseek-v4.1-flash": PriceEntry(input_per_mtok=0.06, output_per_mtok=0.6),
        "claude-opus-5-5": PriceEntry(input_per_mtok=4.0, output_per_mtok=20.0),
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_policy(path: str | None = None) -> AgentPolicy:
    p = Path(path or get_settings().policy_file)
    if not p.exists():
        return AgentPolicy()
    data = yaml.safe_load(p.read_text()) or {}
    return AgentPolicy.model_validate(data)
