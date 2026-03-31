"""
Pydantic configuration schemas for ProtoNeo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from dotenv import load_dotenv
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from fastapi import APIRouter
    from ..knowledge.types import DomainConfig, Parser
    from ..export.types import Exporter
    from ..tools.types import Tool

# Load .env from project root
_project_root = Path(__file__).resolve().parents[2]
for _candidate in [_project_root / ".env"]:
    if _candidate.exists():
        load_dotenv(_candidate, override=True)
        break


class LLMProviderConfig(BaseModel):
    """Configuration for a single LLM provider."""

    api_key: str | None = None
    base_url: str | None = None


class AgentConfig(BaseModel):
    """Configuration for a single agent role."""

    role: str
    model: str
    system_prompt: str = ""
    focus: str = ""
    max_tokens: int = 4096
    grounding: list[dict[str, Any]] = Field(default_factory=list)
    temperature: float | None = None
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None


class PhaseConfig(BaseModel):
    """Configuration for a single deliberation phase."""

    name: str
    mode: str = Field(description="'parallel', 'round_robin', or 'sequential'")
    agents: list[str]
    max_rounds: int = 1
    visibility: str = "open"
    input: str | None = None


class DeliberationConfig(BaseModel):
    """Configuration for the deliberation pattern."""

    pattern: str = "independent_synthesis"
    phases: list[PhaseConfig] = Field(default_factory=list)


class StorageConfig(BaseModel):
    """Storage backend configuration."""

    session_dir: str = Field(
        default_factory=lambda: str(Path(__file__).resolve().parents[2] / "data" / "sessions")
    )


class ProtoNeoConfig(BaseModel):
    """Root configuration for the ProtoNeo kernel."""

    providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    deliberation: DeliberationConfig = Field(default_factory=DeliberationConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    @classmethod
    def from_env(cls) -> "ProtoNeoConfig":
        """Build config from environment variables.

        Only initializes providers that have credentials available.
        No hardcoded endpoints or model names. Users configure local
        and LAN endpoints through the Settings UI or settings.json.
        """
        providers: dict[str, LLMProviderConfig] = {}

        # Default local key for OpenAI-compatible endpoints (LM Studio, Ollama, etc.)
        _local_key = os.getenv("LLM_API_KEY", "sk-local")
        _local_url = os.getenv("LLM_BASE_URL")
        if _local_url:
            providers["local"] = LLMProviderConfig(api_key=_local_key, base_url=_local_url)

        # OpenRouter
        or_key = os.getenv("OPENROUTER_API_KEY")
        if or_key:
            providers["openrouter"] = LLMProviderConfig(api_key=or_key)

        # Cloud providers: try OAuth tokens first (subscription logins),
        # then fall back to environment variable API keys.
        try:
            from ..llm.providers.registry import get_provider_registry
            oauth_registry = get_provider_registry()
        except Exception:
            oauth_registry = None

        for provider_name, env_vars in [
            # ("anthropic", ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"]),  # DISABLED
            ("openai", ["OPENAI_API_KEY"]),
        ]:
            key = None
            if oauth_registry:
                key = oauth_registry.resolve_api_key(provider_name)
            if not key:
                for env_var in env_vars:
                    key = os.getenv(env_var)
                    if key:
                        break
            if key:
                providers[provider_name] = LLMProviderConfig(api_key=key)

        return cls(
            providers=providers,
        )


@dataclass
class AppRegistration:
    """Constrained interface provided to apps during kernel registration.

    Applications use this to contribute parsers, exporters, and tools
    to the kernel's registries. They cannot access the FastAPI app,
    app.state, or kernel internals directly.
    """

    _doc_processor: Any = field(repr=False)
    _tool_registry: Any = field(repr=False)
    _export_registry: Any = field(repr=False)

    def register_parser(self, parser: Parser, priority: int = 0) -> None:
        self._doc_processor.register_parser(parser, priority)

    def register_exporter(self, exporter: Exporter) -> None:
        self._export_registry.register(exporter)

    def register_tool(self, tool: Tool) -> None:
        self._tool_registry.register(tool)


@dataclass
class AppManifest:
    """Contract between an application and the kernel."""

    name: str
    display_name: str
    version: str
    description: str

    # Routing: pre-built router, kernel mounts under /api/apps/{name}/
    router: APIRouter | None = None

    # Registration callback: receives constrained interface, not raw FastAPI
    on_register: Callable[[AppRegistration], None] | None = None

    # Domain knowledge (injected into kernel knowledge modules)
    domain_config: DomainConfig | None = None

    # Application directories
    profile_dir: Path | None = None
    prompt_dir: Path | None = None

    # Output rendering hints for the UI
    result_schema: dict | None = None
    score_fields: list[dict] | None = None

    # Pipeline stages contributed by the app
    pipeline_stages: list[str] = field(default_factory=list)
