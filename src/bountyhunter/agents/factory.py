"""pydantic-ai factory for the explicit, opt-in OpenAI API fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from bountyhunter.agents.pai_sketch import ROLE_INSTRUCTIONS
from bountyhunter.agents.reasoning import claude_effort, output_token_budget
from bountyhunter.agents.stubs import AgentStub, stub_for
from bountyhunter.models import (
    ANTHROPIC_MODEL_PRESETS,
    OPENAI_MODEL_PRESETS,
    AgentRole,
    AgentRoleConfig,
    ModelPolicy,
    ModelProvider,
)

if TYPE_CHECKING:
    from pydantic_ai import Agent

# Re-exported for backward compatibility; the canonical lists live in models.
__all__ = [
    "OPENAI_MODEL_PRESETS",
    "ANTHROPIC_MODEL_PRESETS",
    "ModelConfigurationError",
    "build_agent",
    "policy_for",
]


class ModelConfigurationError(ValueError):
    pass


def policy_for(config: AgentRoleConfig, *, use_llm: bool | None = None) -> ModelPolicy:
    return ModelPolicy(
        provider=config.provider,
        model=config.model,
        reasoning=config.reasoning,
        enabled=config.enabled,
        use_llm=config.use_llm if use_llm is None else use_llm,
    )


def _secret_value(api_key: SecretStr | str | None) -> str | None:
    if isinstance(api_key, SecretStr):
        return api_key.get_secret_value()
    return api_key


def build_agent(
    role: AgentRole,
    policy: ModelPolicy | AgentRoleConfig,
    *,
    api_key: SecretStr | str | None = None,
    deps_type: type[Any] | None = None,
    output_type: Any = str,
) -> AgentStub | Agent[Any, Any]:
    """Return a local stub or an OpenAI Responses-backed pydantic-ai Agent.

    No request is made while constructing an Agent. Live calls require both the
    per-role ``use_llm`` opt-in and an ephemeral/environment API key.
    """
    if isinstance(policy, AgentRoleConfig):
        policy = policy_for(policy)
    if not policy.enabled or not policy.use_llm or policy.provider is ModelProvider.LOCAL:
        return stub_for(role)

    from pydantic_ai import Agent

    from bountyhunter.organizer.service import HuntDeps

    if policy.provider is ModelProvider.OPENAI:
        model = _build_openai_model(role, policy, api_key)
    elif policy.provider is ModelProvider.ANTHROPIC:
        model = _build_anthropic_model(role, policy, api_key)
    else:
        raise ModelConfigurationError(f"Unsupported model provider: {policy.provider}")

    return Agent(
        model,
        output_type=output_type,
        deps_type=deps_type or HuntDeps,
        instructions=ROLE_INSTRUCTIONS[role],
        name=f"bountyhunter_{role.value}",
        retries=1,
    )


def _require_key(api_key: SecretStr | str | None, *, provider: str, dialog: str, env: str) -> str:
    key = (_secret_value(api_key) or "").strip()
    if not key:
        raise ModelConfigurationError(
            f"{provider} is enabled for this role, but no API key is configured. "
            f"Use Target → {dialog} or set {env}."
        )
    return key


def _build_openai_model(role: AgentRole, policy: ModelPolicy, api_key: SecretStr | str | None):
    key = _require_key(
        api_key, provider="OpenAI", dialog="OpenAI connection…", env="OPENAI_API_KEY"
    )
    from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
    from pydantic_ai.providers.openai import OpenAIProvider

    settings = OpenAIResponsesModelSettings(
        openai_reasoning_effort=policy.reasoning.value,
        openai_store=False,
        openai_text_verbosity="medium" if role is AgentRole.REPORTER else "low",
        timeout=90,
    )
    return OpenAIResponsesModel(
        policy.model,  # type: ignore[arg-type]
        provider=OpenAIProvider(api_key=key),
        settings=settings,
    )


def _build_anthropic_model(role: AgentRole, policy: ModelPolicy, api_key: SecretStr | str | None):
    key = _require_key(
        api_key, provider="Anthropic", dialog="Anthropic connection…", env="ANTHROPIC_API_KEY"
    )
    from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
    from pydantic_ai.providers.anthropic import AnthropicProvider

    verbose = role is AgentRole.REPORTER
    settings = AnthropicModelSettings(
        max_tokens=output_token_budget(policy.reasoning, verbose=verbose),
        timeout=90,
    )
    effort = claude_effort(policy.reasoning)
    if effort is not None:
        settings["anthropic_effort"] = effort  # low/medium/high/xhigh/max
    else:
        # ReasoningLevel.NONE → no extended thinking.
        settings["anthropic_thinking"] = {"type": "disabled"}
    return AnthropicModel(
        policy.model,  # type: ignore[arg-type]
        provider=AnthropicProvider(api_key=key),
        settings=settings,
    )
