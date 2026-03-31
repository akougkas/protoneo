"""Static model catalogs for subscription providers.

These are the exact models available through each subscription,
matching what /model shows in Claude Code, Codex CLI, and Gemini CLI.
No API models, no models.dev. Just the subscription models.
"""

# DISABLED: Anthropic provider removed from ProtoNeo.
# Claude Max subscription models preserved as reference only.
# ANTHROPIC_SUBSCRIPTION_MODELS = [
#     {"id": "claude-opus-4-6", "name": "Claude Opus 4.6", "context_length": 1_000_000, "reasoning": True, "source": "anthropic", "provider_type": "subscription"},
#     {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "context_length": 200_000, "reasoning": True, "source": "anthropic", "provider_type": "subscription"},
#     {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "context_length": 200_000, "reasoning": False, "source": "anthropic", "provider_type": "subscription"},
# ]
ANTHROPIC_SUBSCRIPTION_MODELS: list = []  # Empty: provider disabled

# ChatGPT Plus/Pro subscription models (from Codex CLI /model)
OPENAI_SUBSCRIPTION_MODELS = [
    {
        "id": "gpt-5.4",
        "name": "GPT-5.4",
        "context_length": 272_000,
        "reasoning": True,
        "source": "openai",
        "provider_type": "subscription",
    },
    {
        "id": "gpt-5.4-mini",
        "name": "GPT-5.4 Mini",
        "context_length": 272_000,
        "reasoning": True,
        "source": "openai",
        "provider_type": "subscription",
    },
    {
        "id": "gpt-5.3-codex",
        "name": "GPT-5.3 Codex",
        "context_length": 272_000,
        "reasoning": True,
        "source": "openai",
        "provider_type": "subscription",
    },
    {
        "id": "gpt-5.2-codex",
        "name": "GPT-5.2 Codex",
        "context_length": 272_000,
        "reasoning": True,
        "source": "openai",
        "provider_type": "subscription",
    },
    {
        "id": "gpt-5.2",
        "name": "GPT-5.2",
        "context_length": 272_000,
        "reasoning": True,
        "source": "openai",
        "provider_type": "subscription",
    },
    {
        "id": "gpt-5.1-codex-max",
        "name": "GPT-5.1 Codex Max",
        "context_length": 272_000,
        "reasoning": True,
        "source": "openai",
        "provider_type": "subscription",
    },
    {
        "id": "gpt-5.1-codex-mini",
        "name": "GPT-5.1 Codex Mini",
        "context_length": 272_000,
        "reasoning": True,
        "source": "openai",
        "provider_type": "subscription",
    },
]

SUBSCRIPTION_CATALOGS = {
    "anthropic": ANTHROPIC_SUBSCRIPTION_MODELS,
    "openai": OPENAI_SUBSCRIPTION_MODELS,
}
