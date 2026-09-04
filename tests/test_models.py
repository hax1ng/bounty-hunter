from __future__ import annotations

import pytest

from bountyhunter.models import (
    AgentRole,
    AgentRoleConfig,
    Finding,
    SurfaceSlice,
    Target,
    parse_scope_text,
)


def test_parse_scope_text_strips_comments() -> None:
    text = """
    *.example.com
    # ignore me
    api.example.com
    *.example.com

    """
    assert parse_scope_text(text) == ["*.example.com", "api.example.com"]


def test_name_required() -> None:
    with pytest.raises(ValueError):
        Target(name="   ")


def test_fingerprint_ignores_updated_at() -> None:
    t = Target(name="F", authorized=True, in_scope=["a.test"])
    a = t.fingerprint()
    t.touch()
    assert t.fingerprint() == a
    t.notes = "x"
    assert t.fingerprint() != a


@pytest.mark.parametrize("model", [SurfaceSlice, Finding])
def test_file_backed_artifact_ids_cannot_escape_target_folder(model) -> None:
    fields = {"title": "bad", "asset": "x.test"} if model is SurfaceSlice else {"title": "bad"}
    with pytest.raises(ValueError, match="Artifact id"):
        model(id="../../escape", **fields)


def test_agent_config_key_must_match_embedded_role() -> None:
    with pytest.raises(ValueError, match="contains role"):
        Target(
            name="bad roles",
            agent_config={
                "solver": AgentRoleConfig(role=AgentRole.REPORTER),
            },
        )


def test_continuous_hunt_settings_are_bounded() -> None:
    target = Target(
        name="Continuous",
        continuous_hunt=True,
        hunt_time_limit_minutes=45,
        hunt_cycle_delay_seconds=10,
    )
    assert target.continuous_hunt is True
    assert target.hunt_time_limit_minutes == 45
    assert target.hunt_cycle_delay_seconds == 10
    with pytest.raises(ValueError):
        Target(name="Bad delay", hunt_cycle_delay_seconds=0)
