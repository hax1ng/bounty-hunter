from __future__ import annotations

import pytest

from bountyhunter.agents.factory import build_agent
from bountyhunter.agents.stubs import AGENT_STUBS, assistant_reply
from bountyhunter.models import (
    AgentRole,
    HuntEvent,
    HuntState,
    ModelPolicy,
    ModelProvider,
    ReasoningLevel,
    default_agent_config,
    model_presets_for,
)
from bountyhunter.safety import reporter_submit_blocked


def test_all_roles_stubbed() -> None:
    assert set(AGENT_STUBS) == set(AgentRole)


def test_reporter_never_submits() -> None:
    assert AGENT_STUBS[AgentRole.REPORTER].auto_submits is False
    with pytest.raises(ValueError, match="never auto-submits"):
        raise reporter_submit_blocked()


def test_assistant_does_not_hunt() -> None:
    assert AGENT_STUBS[AgentRole.ASSISTANT].hunts is False
    reply = assistant_reply("please submit this to hackerone", target_name="Acme", authorized=True)
    assert "never" in reply.lower()


def test_assistant_no_target() -> None:
    reply = assistant_reply("hello", target_name=None, authorized=None)
    assert "No Target" in reply


def test_stub_assistant_can_summarize_backend_logs() -> None:
    reply = assistant_reply(
        "what is the backend status and latest log?",
        target_name="Acme",
        authorized=True,
        hunt_state=HuntState.HUNTING.value,
        hunt_note="Testing bounded slices",
        solvers_alive=2,
        recent_events=[HuntEvent(message="Solver queued", phase=HuntState.HUNTING)],
    )
    assert "hunting" in reply
    assert "2 Solver" in reply
    assert "Solver queued" in reply


@pytest.mark.asyncio
async def test_stub_run() -> None:
    text = await AGENT_STUBS[AgentRole.MAPPING].run()
    assert "stub" in text.lower()


def test_default_agent_config_has_every_role() -> None:
    cfg = default_agent_config()
    for role in AgentRole:
        assert role.value in cfg


def test_factory_defaults_to_no_key_stub() -> None:
    built = build_agent(AgentRole.ASSISTANT, ModelPolicy())
    assert built is AGENT_STUBS[AgentRole.ASSISTANT]


def test_factory_builds_openai_responses_agent_without_calling_api() -> None:
    from pydantic_ai import Agent
    from pydantic_ai.models.openai import OpenAIResponsesModel

    built = build_agent(
        AgentRole.ASSISTANT,
        ModelPolicy(
            model="gpt-5.6-luna",
            reasoning=ReasoningLevel.LOW,
            use_llm=True,
        ),
        api_key="sk-test-not-a-real-key",
    )
    assert isinstance(built, Agent)
    assert isinstance(built.model, OpenAIResponsesModel)


def test_codex_subscription_schema_is_strict() -> None:
    from bountyhunter.agents.codex_subscription import _parse_output, _structured_schema
    from bountyhunter.agents.live import OrganizerBrief

    schema = _structured_schema(OrganizerBrief)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"summary", "cautions"}
    recovered = _parse_output(
        'Here is the object:\n{"summary":"bounded","cautions":[]}',
        OrganizerBrief,
    )
    assert recovered.summary == "bounded"


def test_codex_invalid_structured_output_has_safe_schema_detail() -> None:
    from bountyhunter.agents.codex_subscription import CodexSubscriptionError, _parse_output
    from bountyhunter.agents.live import OrganizerBrief

    with pytest.raises(CodexSubscriptionError, match=r"OrganizerBrief.*summary"):
        _parse_output('{"cautions":[]}', OrganizerBrief)


@pytest.mark.asyncio
async def test_live_adapter_prefers_subscription_without_api_key(tmp_path, monkeypatch) -> None:
    from bountyhunter.agents import live
    from bountyhunter.agents.codex_subscription import CodexSubscriptionResult
    from bountyhunter.events import EventBus
    from bountyhunter.models import Target
    from bountyhunter.organizer.service import HuntDeps, KillSwitch
    from bountyhunter.session import Session
    from bountyhunter.settings import AppSettings

    session = Session(settings=AppSettings(), settings_path=tmp_path / "settings.json")
    target = Target(name="Subscription", authorized=True, in_scope=["local.test"])
    target.agent_config[AgentRole.ORGANIZER.value].use_llm = True
    session.new_unsaved(target)
    session.openai.codex_authenticated = True

    async def fake_subscription(**_kwargs):
        return CodexSubscriptionResult(
            output=live.OrganizerBrief(summary="bounded", cautions=[]),
            total_tokens=42,
        )

    monkeypatch.setattr(live, "run_codex_subscription", fake_subscription)
    deps = HuntDeps(
        session=session,
        target=target,
        event_bus=EventBus(),
        kill_switch=KillSwitch(),
        hunt_id="test-hunt",
    )
    result = await live.organizer_brief(deps)
    assert result.summary == "bounded"
    assert any("ChatGPT subscription" in event.message for event in session.events)


def test_anthropic_model_presets_are_opus_and_sonnet() -> None:
    presets = model_presets_for(ModelProvider.ANTHROPIC)
    assert any("opus" in name for name in presets)
    assert any("sonnet" in name for name in presets)
    assert model_presets_for(ModelProvider.LOCAL) == []


def test_local_provider_coerces_use_llm_off_and_is_not_live() -> None:
    from bountyhunter.models import AgentRoleConfig

    cfg = AgentRoleConfig(role=AgentRole.SOLVER, provider=ModelProvider.LOCAL, use_llm=True)
    # A local-stub provider must never be treated as a live hosted call.
    assert cfg.use_llm is False
    assert cfg.is_live is False

    live = AgentRoleConfig(
        role=AgentRole.SOLVER, provider=ModelProvider.ANTHROPIC, use_llm=True, enabled=True
    )
    assert live.is_live is True
    assert AgentRoleConfig(
        role=AgentRole.SOLVER, provider=ModelProvider.ANTHROPIC, use_llm=True, enabled=False
    ).is_live is False


def test_target_setup_plan_is_reviewable_and_cannot_authorize() -> None:
    from bountyhunter.agents.live import TargetSetupPlan
    from bountyhunter.models import Platform

    plan = TargetSetupPlan(
        name="  Example  ",
        platform=Platform.HACKERONE,
        in_scope=[" api.example.test ", "api.example.test"],
        out_of_scope=["blog.example.test"],
        notes="  Follow the rate limit.  ",
    )
    assert plan.name == "Example"
    assert plan.in_scope == ["api.example.test"]
    assert "authorized" not in type(plan).model_fields
    with pytest.raises(ValueError, match="same asset"):
        TargetSetupPlan(
            name="Overlap",
            in_scope=["same.test"],
            out_of_scope=["same.test"],
        )


def test_factory_builds_anthropic_model_without_calling_api() -> None:
    from pydantic_ai import Agent
    from pydantic_ai.models.anthropic import AnthropicModel

    built = build_agent(
        AgentRole.SOLVER,
        ModelPolicy(
            provider=ModelProvider.ANTHROPIC,
            model="claude-opus-4-8",
            reasoning=ReasoningLevel.XHIGH,
            use_llm=True,
        ),
        api_key="sk-ant-not-a-real-key",
    )
    assert isinstance(built, Agent)
    assert isinstance(built.model, AnthropicModel)
    assert built.model.settings["anthropic_effort"] == "xhigh"


def test_factory_anthropic_none_reasoning_disables_thinking() -> None:
    built = build_agent(
        AgentRole.DEDUP,
        ModelPolicy(
            provider=ModelProvider.ANTHROPIC,
            model="claude-sonnet-4-6",
            reasoning=ReasoningLevel.NONE,
            use_llm=True,
        ),
        api_key="sk-ant-x",
    )
    assert built.model.settings["anthropic_thinking"] == {"type": "disabled"}
    assert "anthropic_effort" not in built.model.settings


def test_factory_anthropic_requires_key() -> None:
    from bountyhunter.agents.factory import ModelConfigurationError

    with pytest.raises(ModelConfigurationError, match="ANTHROPIC_API_KEY"):
        build_agent(
            AgentRole.MAPPING,
            ModelPolicy(provider=ModelProvider.ANTHROPIC, model="claude-opus-4-8", use_llm=True),
            api_key=None,
        )


def test_reasoning_maps_to_claude_effort_and_budget() -> None:
    from bountyhunter.agents.reasoning import claude_effort, output_token_budget

    assert claude_effort(ReasoningLevel.NONE) is None
    assert claude_effort(ReasoningLevel.MAX) == "max"
    assert claude_effort(ReasoningLevel.XHIGH) == "xhigh"
    # Higher reasoning gets a larger output budget; verbose roles get more room.
    assert output_token_budget(ReasoningLevel.MAX) > output_token_budget(ReasoningLevel.LOW)
    assert output_token_budget(ReasoningLevel.HIGH, verbose=True) > output_token_budget(
        ReasoningLevel.HIGH
    )


def test_claude_subscription_coerces_structured_output() -> None:
    from bountyhunter.agents.claude_subscription import (
        ClaudeSubscriptionError,
        _coerce_output,
        _extract_total_tokens,
    )
    from bountyhunter.agents.live import OrganizerBrief

    payload = {
        "is_error": False,
        "subtype": "success",
        "structured_output": {"summary": "bounded", "cautions": ["scope"]},
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    out = _coerce_output(payload, OrganizerBrief)
    assert isinstance(out, OrganizerBrief)
    assert out.summary == "bounded"
    assert _extract_total_tokens(payload) == 30

    # A JSON string in ``result`` (older CLI) is also accepted.
    out2 = _coerce_output(
        {"result": '{"summary": "x", "cautions": []}'}, OrganizerBrief
    )
    assert out2.summary == "x"

    with pytest.raises(ClaudeSubscriptionError):
        _coerce_output({"result": ""}, OrganizerBrief)


@pytest.mark.asyncio
async def test_live_adapter_prefers_anthropic_subscription_without_api_key(
    tmp_path, monkeypatch
) -> None:
    from bountyhunter.agents import live
    from bountyhunter.agents.claude_subscription import ClaudeSubscriptionResult
    from bountyhunter.events import EventBus
    from bountyhunter.models import Target
    from bountyhunter.organizer.service import HuntDeps, KillSwitch
    from bountyhunter.session import Session
    from bountyhunter.settings import AppSettings

    session = Session(settings=AppSettings(), settings_path=tmp_path / "settings.json")
    target = Target(name="ClaudeSub", authorized=True, in_scope=["local.test"])
    organizer = target.agent_config[AgentRole.ORGANIZER.value]
    organizer.provider = ModelProvider.ANTHROPIC
    organizer.model = "claude-sonnet-4-6"
    organizer.use_llm = True
    session.new_unsaved(target)
    session.anthropic.claude_authenticated = True
    session.anthropic.subscription_type = "max"

    captured = {}

    async def fake_claude(**kwargs):
        captured.update(kwargs)
        return ClaudeSubscriptionResult(
            output=live.OrganizerBrief(summary="claude bounded", cautions=[]),
            total_tokens=99,
        )

    # If this route touched the OpenAI/Codex path the test would fail loudly.
    monkeypatch.setattr(live, "run_claude_subscription", fake_claude)
    deps = HuntDeps(
        session=session,
        target=target,
        event_bus=EventBus(),
        kill_switch=KillSwitch(),
        hunt_id="claude-hunt",
    )
    result = await live.organizer_brief(deps)
    assert result.summary == "claude bounded"
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["reasoning"] is organizer.reasoning
    assert any("Claude subscription" in event.message for event in session.events)
