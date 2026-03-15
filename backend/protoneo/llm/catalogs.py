"""Static model catalogs for subscription providers.

These are the exact models available through each subscription,
matching what /model shows in Claude Code, Codex CLI, and Gemini CLI.
No API models, no models.dev. Just the subscription models.
"""

# Claude Max subscription models (from Claude Code /model)
ANTHROPIC_SUBSCRIPTION_MODELS = [
    {
        "id": "claude-opus-4-6",
        "name": "Claude Opus 4.6",
        "context_length": 1_000_000,
        "reasoning": True,
        "source": "anthropic",
        "provider_type": "subscription",
    },
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
        "context_length": 200_000,
        "reasoning": True,
        "source": "anthropic",
        "provider_type": "subscription",
    },
    {
        "id": "claude-haiku-4-5",
        "name": "Claude Haiku 4.5",
        "context_length": 200_000,
        "reasoning": False,
        "source": "anthropic",
        "provider_type": "subscription",
    },
]

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

# Gemini CLI subscription models (from pi-ai generate-models.ts, Gemini 3 only)
GOOGLE_SUBSCRIPTION_MODELS = [
    {
        "id": "gemini-3.1-pro-preview",
        "name": "Gemini 3.1 Pro",
        "context_length": 1_000_000,
        "reasoning": True,
        "source": "google",
        "provider_type": "subscription",
    },
    {
        "id": "gemini-3-flash-preview",
        "name": "Gemini 3 Flash",
        "context_length": 1_000_000,
        "reasoning": True,
        "source": "google",
        "provider_type": "subscription",
    },
]

# Antigravity subscription models (from pi-ai models.generated.ts)
GOOGLE_ANTIGRAVITY_SUBSCRIPTION_MODELS = [
    {
        "id": "gemini-3.1-pro-high",
        "name": "Gemini 3.1 Pro (High)",
        "context_length": 1_000_000,
        "reasoning": True,
        "source": "google-antigravity",
        "provider_type": "subscription",
    },
    {
        "id": "gemini-3.1-pro-low",
        "name": "Gemini 3.1 Pro (Low)",
        "context_length": 1_000_000,
        "reasoning": True,
        "source": "google-antigravity",
        "provider_type": "subscription",
    },
    {
        "id": "gemini-3-flash",
        "name": "Gemini 3 Flash",
        "context_length": 1_000_000,
        "reasoning": True,
        "source": "google-antigravity",
        "provider_type": "subscription",
    },
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6 (Thinking)",
        "context_length": 200_000,
        "reasoning": True,
        "source": "google-antigravity",
        "provider_type": "subscription",
    },
    {
        "id": "claude-opus-4-6-thinking",
        "name": "Claude Opus 4.6 (Thinking)",
        "context_length": 200_000,
        "reasoning": True,
        "source": "google-antigravity",
        "provider_type": "subscription",
    },
    # gpt-oss-120b-medium: listed in Antigravity IDE but returns 400
    # on all endpoints. Commented out until Google enables it.
    # {
    #     "id": "gpt-oss-120b-medium",
    #     "name": "GPT-OSS 120B (Medium)",
    #     "context_length": 128_000,
    #     "reasoning": False,
    #     "source": "google-antigravity",
    #     "provider_type": "subscription",
    # },
]

SUBSCRIPTION_CATALOGS = {
    "anthropic": ANTHROPIC_SUBSCRIPTION_MODELS,
    "openai": OPENAI_SUBSCRIPTION_MODELS,
    "google": GOOGLE_SUBSCRIPTION_MODELS,
    "google-antigravity": GOOGLE_ANTIGRAVITY_SUBSCRIPTION_MODELS,
}
