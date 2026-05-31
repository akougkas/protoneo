"""
LLM type definitions.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ModelCapability(str, Enum):
    VISION = "vision"
    STRUCTURED_OUTPUT = "structured_output"
    FUNCTION_CALLING = "function_calling"
    STREAMING = "streaming"
    EXTENDED_THINKING = "extended_thinking"
    REASONING_CONTROL = "reasoning_control"


class ModelQuirk(str, Enum):
    """Provider/model response behaviors that affect request and output handling."""

    THINK_TAGS = "think_tags"
    THINKING_TAGS = "thinking_tags"
    REASONING_CONTENT_FIELD = "reasoning_content_field"
    HIDDEN_REASONING_FIELD = "hidden_reasoning_field"


class StructuredOutputReliability(str, Enum):
    UNKNOWN = "unknown"
    BASIC = "basic"
    STRONG = "strong"


class LatencyClass(str, Enum):
    UNKNOWN = "unknown"
    FAST = "fast"
    BALANCED = "balanced"
    SLOW = "slow"


class ModelTier(str, Enum):
    """Where the model runs and how it's billed."""
    LOCAL = "local"           # Homelab nodes, no cost
    CLOUD_FREE = "cloud_free" # OpenRouter free tier
    CLOUD_API = "cloud_api"   # Standard API key billing
    SUBSCRIPTION = "subscription"  # Claude Max, ChatGPT Plus, etc.


class ModelInfo(BaseModel):
    """Describes a model's identity, routing, and capabilities."""

    model_id: str = Field(description="ProtoNeo model identifier used by agents")
    provider: str = Field(description="Provider name or endpoint id: lan-mini, localhost-lmstudio, openrouter, anthropic, openai, etc.")
    litellm_model: str | None = Field(
        default=None,
        description="LiteLLM model string if different from model_id (e.g. 'openai/Qwen35-Distilled-i1-Q4_K_M')",
    )
    api_base: str | None = Field(default=None, description="Per-model endpoint URL override")
    capabilities: set[ModelCapability] = Field(default_factory=set)
    quirks: set[ModelQuirk] = Field(default_factory=set)
    max_context: int = Field(default=0)
    speed_tps: int = Field(default=0, description="Approximate tokens per second for scheduling")
    latency_class: LatencyClass = Field(default=LatencyClass.UNKNOWN)
    structured_output: StructuredOutputReliability = Field(default=StructuredOutputReliability.UNKNOWN)
    runtime_location: str = Field(default="", description="localhost, lan, or remote")
    cost_per_input_token: float = 0.0
    cost_per_output_token: float = 0.0
    tier: ModelTier = Field(default=ModelTier.LOCAL, description="Billing tier for cost/privacy decisions")
    display_name: str = Field(default="", description="Human-readable name for UI display")

    @property
    def effective_model(self) -> str:
        """The model string to pass to LiteLLM."""
        return self.litellm_model or self.model_id

    @property
    def is_private(self) -> bool:
        """True if data stays on-prem (local endpoints only)."""
        return self.tier == ModelTier.LOCAL


class TokenUsage(BaseModel):
    """Token counts from a single LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


class LLMResponse(BaseModel):
    """Unified response from an LLM call."""

    content: str
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)
