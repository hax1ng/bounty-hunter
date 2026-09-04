from __future__ import annotations

from pathlib import Path

import pytest

from bountyhunter.agents.factory import ModelConfigurationError
from bountyhunter.models import ModelProvider, Target
from bountyhunter.organizer.service import require_live_models_ready
from bountyhunter.providers import AnthropicConnection, _parse_claude_status
from bountyhunter.session import Session
from bountyhunter.settings import AppSettings


def _session(tmp_path: Path, **settings) -> Session:
    return Session(settings=AppSettings(**settings), settings_path=tmp_path / "settings.json")


def test_parse_claude_status_tolerates_noise() -> None:
    stdout = 'warning: something\n{"loggedIn": true, "authMethod": "claude.ai"}\n'
    assert _parse_claude_status(stdout) == {"loggedIn": True, "authMethod": "claude.ai"}
    assert _parse_claude_status("") == {}
    assert _parse_claude_status("not json at all") == {}


def _fake_status(payload: str):
    class _Result:
        returncode = 0
        stdout = payload
        stderr = ""

    def _run(_cmd, **_kwargs):
        return _Result()

    return _run


def test_anthropic_subscription_detected(monkeypatch) -> None:
    from bountyhunter import providers

    monkeypatch.setattr(providers.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        providers.subprocess,
        "run",
        _fake_status(
            '{"loggedIn": true, "authMethod": "claude.ai", "subscriptionType": "max"}'
        ),
    )
    conn = AnthropicConnection()
    assert conn.refresh_subscription() is True
    assert conn.subscription_authenticated is True
    assert conn.subscription_type == "max"
    assert conn.ready is True
    assert "subscription" in conn.status.lower()


def test_anthropic_api_key_login_is_not_a_subscription(monkeypatch) -> None:
    from bountyhunter import providers

    monkeypatch.setattr(providers.shutil, "which", lambda _name: "/usr/bin/claude")
    monkeypatch.setattr(
        providers.subprocess,
        "run",
        _fake_status('{"loggedIn": true, "authMethod": "apiKey"}'),
    )
    conn = AnthropicConnection()
    # Logged in with a raw API key is not the included subscription entitlement.
    assert conn.refresh_subscription() is False
    assert conn.subscription_authenticated is False


def test_anthropic_missing_cli(monkeypatch) -> None:
    from bountyhunter import providers

    monkeypatch.setattr(providers.shutil, "which", lambda _name: None)
    conn = AnthropicConnection()
    assert conn.refresh_subscription() is False
    assert "not installed" in conn.claude_auth_method


def test_connection_for_routes_by_provider(tmp_path: Path) -> None:
    session = _session(tmp_path)
    assert session.connection_for(ModelProvider.ANTHROPIC) is session.anthropic
    assert session.connection_for(ModelProvider.OPENAI) is session.openai
    assert session.connection_for(ModelProvider.LOCAL) is session.openai


def test_anthropic_subscription_first_preference_loaded(tmp_path: Path) -> None:
    session = _session(tmp_path, anthropic_subscription_first=False)
    assert session.anthropic.use_subscription_first is False


def test_live_anthropic_role_requires_credentials(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.anthropic.use_subscription_first = False
    session.anthropic.use_session_key("")
    target = Target(name="AnthLive", authorized=True, in_scope=["live.test"])
    cfg = target.agent_config["mapping"]
    cfg.provider = ModelProvider.ANTHROPIC
    cfg.model = "claude-opus-4-8"
    cfg.use_llm = True
    with pytest.raises(ModelConfigurationError, match="Anthropic"):
        require_live_models_ready(session, target)


def test_live_anthropic_role_accepts_subscription(tmp_path: Path, monkeypatch) -> None:
    session = _session(tmp_path)
    session.anthropic.use_session_key("")
    monkeypatch.setattr(AnthropicConnection, "refresh_subscription", lambda _self: True)
    target = Target(name="AnthSub", authorized=True, in_scope=["live.test"])
    cfg = target.agent_config["mapping"]
    cfg.provider = ModelProvider.ANTHROPIC
    cfg.model = "claude-sonnet-4-6"
    cfg.use_llm = True
    # Should not raise: subscription satisfies readiness without an API key.
    require_live_models_ready(session, target)


def test_anthropic_api_key_not_persisted(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.anthropic.use_session_key("sk-ant-super-secret")
    session.new_unsaved(Target(name="AnthSecret", authorized=True, in_scope=["s.test"]))
    session.save_as(tmp_path / "anth-secret.bountyhunt")
    manifest = (session.path / "target.json").read_text(encoding="utf-8")
    assert "sk-ant-super-secret" not in manifest
