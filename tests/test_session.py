from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bountyhunter.agents.factory import ModelConfigurationError
from bountyhunter.models import (
    Finding,
    FindingStatus,
    HuntState,
    Platform,
    Severity,
    SliceKind,
    SurfaceSlice,
    Target,
)
from bountyhunter.organizer.service import Organizer
from bountyhunter.safety import AuthorizationError
from bountyhunter.session import Session
from bountyhunter.settings import AppSettings, load_settings
from bountyhunter.store import create_target


def _session(tmp_path: Path) -> Session:
    settings_file = tmp_path / "settings.json"
    return Session(settings=AppSettings(), settings_path=settings_file)


def test_dirty_new_unsaved(tmp_path: Path) -> None:
    session = _session(tmp_path)
    target = Target(
        name="Mem",
        platform=Platform.HACKERONE,
        in_scope=["a.test"],
        authorized=True,
    )
    session.new_unsaved(target)
    assert session.dirty is True
    assert session.window_title.endswith("*")
    assert session.path is None


def test_save_clears_dirty(tmp_path: Path) -> None:
    session = _session(tmp_path)
    target, dest = create_target(
        name="Disk",
        platform=Platform.HACKERONE,
        in_scope=["d.test"],
        out_of_scope=[],
        authorized=True,
        target_dir=tmp_path / "disk",
    )
    session.attach(target, dest, saved=True)
    assert session.dirty is False
    assert "*" not in session.window_title
    session.target.notes = "changed"
    assert session.dirty is True
    session.save()
    assert session.dirty is False


def test_open_and_recent(tmp_path: Path) -> None:
    session = _session(tmp_path)
    _, dest = create_target(
        name="RecentMe",
        platform=Platform.IMMUNEFI,
        in_scope=["r.test"],
        out_of_scope=[],
        authorized=True,
        target_dir=tmp_path / "recent-me",
    )
    session.open_path(dest)
    assert session.target is not None
    assert session.target.name == "RecentMe"
    assert session.settings.recent[0].name == "RecentMe"
    saved = load_settings(session.settings_path)
    assert saved.recent[0].path == str(dest.resolve())


def test_save_as(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.new_unsaved(
        Target(name="As", platform=Platform.YESWEHACK, in_scope=["y.test"], authorized=True)
    )
    dest = tmp_path / "saved-as"
    session.save_as(dest)
    assert session.path == dest.resolve()
    assert session.dirty is False
    other = _session(tmp_path / "other")
    other.open_path(dest)
    assert other.target is not None
    assert other.target.name == "As"


def test_save_as_new_folder_requires_authorization(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.new_unsaved(Target(name="NoAuth", in_scope=["n.test"], authorized=False))
    with pytest.raises(AuthorizationError):
        session.save_as(tmp_path / "no-auth.bountyhunt")


def test_close(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.new_unsaved(Target(name="X", authorized=True))
    session.close()
    assert session.target is None
    assert session.dirty is False
    assert session.window_title == "Bounty Hunter"


def test_organizer_blocks_unauthorized(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.new_unsaved(Target(name="Bad", authorized=False, in_scope=["b.test"]))
    with pytest.raises(AuthorizationError):
        Organizer(session).start()
    assert session.target is not None
    assert session.target.hunt_state is HuntState.IDLE


def test_organizer_blocks_empty_scope(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.new_unsaved(Target(name="Empty", authorized=True, in_scope=[]))
    with pytest.raises(AuthorizationError, match="scope"):
        Organizer(session).start()


def test_organizer_blocks_exact_in_and_out_scope_conflict(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.new_unsaved(
        Target(
            name="Conflict",
            authorized=True,
            in_scope=["conflict.test"],
            out_of_scope=["conflict.test"],
        )
    )
    with pytest.raises(AuthorizationError, match="both in scope and out of scope"):
        Organizer(session).start()


@pytest.mark.asyncio
async def test_scope_is_immutable_during_a_hunt(tmp_path: Path) -> None:
    session = _session(tmp_path)
    target = Target(name="Immutable", authorized=True, in_scope=["a.test"])
    session.new_unsaved(target)
    organizer = Organizer(session)
    organizer.start()
    assert organizer.task is not None
    target.in_scope.append("b.test")

    await organizer.task

    assert target.hunt_state is HuntState.STOPPED
    assert any("Scope changed while work was running" in event.message for event in session.events)


@pytest.mark.asyncio
async def test_pause_resume_restores_the_actual_phase(tmp_path: Path) -> None:
    session = _session(tmp_path)
    target = Target(name="Pause", authorized=True, in_scope=["pause.test"])
    session.new_unsaved(target)
    organizer = Organizer(session)
    organizer.start()
    assert organizer.task is not None
    assert target.hunt_state is HuntState.MAPPING

    organizer.pause()
    assert target.hunt_state is HuntState.PAUSED
    organizer.pause()
    assert target.hunt_state is HuntState.MAPPING

    await organizer.task
    assert target.hunt_state is HuntState.COMPLETE


def test_live_role_requires_openai_key(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.openai.use_subscription_first = False
    session.openai.use_session_key("")
    target = Target(name="Live", authorized=True, in_scope=["live.test"])
    target.agent_config["mapping"].use_llm = True
    session.new_unsaved(target)
    with pytest.raises(ModelConfigurationError, match="API key"):
        Organizer(session).start()


def test_subscription_first_preference_is_loaded_from_settings(tmp_path: Path) -> None:
    session = Session(
        settings=AppSettings(openai_subscription_first=False),
        settings_path=tmp_path / "settings.json",
    )
    assert session.openai.use_subscription_first is False


def test_live_role_accepts_authenticated_subscription(tmp_path: Path, monkeypatch) -> None:
    from bountyhunter.organizer.service import require_live_models_ready
    from bountyhunter.providers import OpenAIConnection

    session = _session(tmp_path)
    session.openai.use_session_key("")
    monkeypatch.setattr(OpenAIConnection, "refresh_subscription", lambda _self: True)
    target = Target(name="Subscription", authorized=True, in_scope=["local.test"])
    target.agent_config["mapping"].use_llm = True
    require_live_models_ready(session, target)


def test_openai_key_is_not_persisted(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.openai.use_session_key("sk-super-secret")
    session.new_unsaved(Target(name="Secret", authorized=True, in_scope=["secret.test"]))
    session.save_as(tmp_path / "secret.bountyhunt")
    manifest = (session.path / "target.json").read_text(encoding="utf-8")
    assert "sk-super-secret" not in manifest


@pytest.mark.asyncio
async def test_stub_hunt_streams_and_persists(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.new_unsaved(Target(name="Pipeline", authorized=True, in_scope=["local.test"]))
    session.save_as(tmp_path / "pipeline.bountyhunt")
    organizer = Organizer(session)
    organizer.start()
    assert organizer.task is not None
    await organizer.task
    assert session.target is not None
    assert session.target.hunt_state is HuntState.COMPLETE
    assert len(session.target.slices) == 2
    assert len(session.target.findings) == 2
    # Local example output is never promoted into a report without real evidence.
    assert len(session.target.reports) == 0
    assert all(f.status is FindingStatus.KILLED for f in session.target.findings)
    assert (session.path / "events.jsonl").read_text(encoding="utf-8").strip()
    sources = [event.source for event in session.events]
    assert sources.index("mapping") < sources.index("solver") < sources.index("oracle")
    assert sources.index("oracle") < sources.index("dedup") < sources.index("devil")
    assert sources.index("devil") < sources.index("evidence") < sources.index("reporter")


@pytest.mark.asyncio
async def test_continuous_hunt_waits_for_stop_after_completed_cycle(tmp_path: Path) -> None:
    session = _session(tmp_path)
    target = Target(
        name="Continuous",
        authorized=True,
        in_scope=["continuous.test"],
        continuous_hunt=True,
        hunt_cycle_delay_seconds=1,
    )
    session.new_unsaved(target)
    organizer = Organizer(session)
    organizer.start()
    assert organizer.task is not None

    for _ in range(100):
        if any("cycle 1 complete; continuing" in event.message for event in session.events):
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("continuous hunt never completed its first cycle")

    first_cycle_findings = {finding.id for finding in target.findings}
    assert organizer.running
    assert target.hunt_state is HuntState.HUNTING
    organizer.stop()
    await organizer.task
    assert target.hunt_state is HuntState.STOPPED
    assert {finding.id for finding in target.findings} == first_cycle_findings


@pytest.mark.asyncio
async def test_hunt_timer_stops_background_task(tmp_path: Path) -> None:
    session = _session(tmp_path)
    target = Target(
        name="Timed",
        authorized=True,
        in_scope=["timed.test"],
        continuous_hunt=True,
    )
    session.new_unsaved(target)
    organizer = Organizer(session)
    organizer.start()
    assert organizer.task is not None
    await organizer._enforce_time_limit(0)
    await organizer.task
    assert target.hunt_state is HuntState.STOPPED
    assert "timer" in session.hunt_note.lower()


@pytest.mark.asyncio
async def test_continuous_hunt_retries_transient_model_error(tmp_path: Path, monkeypatch) -> None:
    import bountyhunter.organizer.service as service

    session = _session(tmp_path)
    session.openai.use_session_key("sk-test")
    target = Target(
        name="Retry",
        authorized=True,
        in_scope=["retry.test"],
        continuous_hunt=True,
        hunt_cycle_delay_seconds=1,
    )
    target.agent_config["organizer"].use_llm = True
    session.new_unsaved(target)

    async def transient_failure(_deps):
        raise RuntimeError("invalid structured output")

    monkeypatch.setattr(service, "organizer_brief", transient_failure)
    organizer = Organizer(session)
    organizer.start()
    assert organizer.task is not None
    for _ in range(100):
        if any("another cycle will retry" in event.message for event in session.events):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("transient model failure was not scheduled for retry")

    assert organizer.running
    assert target.hunt_state is HuntState.HUNTING
    organizer.stop()
    await organizer.task


@pytest.mark.asyncio
async def test_manual_solver_rejects_same_slice_overlap_and_kill_cancels_all(
    tmp_path: Path, monkeypatch
) -> None:
    import bountyhunter.organizer.service as service

    session = _session(tmp_path)
    target = Target(
        name="Manual",
        authorized=True,
        in_scope=["manual.test"],
        slices=[
            SurfaceSlice(id="surface-one", title="One", asset="manual.test"),
            SurfaceSlice(id="surface-two", title="Two", asset="manual.test"),
        ],
    )
    session.new_unsaved(target)
    blocker = asyncio.Event()

    async def slow_solver(_deps, _surface):
        await blocker.wait()
        return None

    monkeypatch.setattr(service, "_solver_for_slice", slow_solver)
    organizer = Organizer(session)
    first = organizer.queue_solver("surface-one")
    await asyncio.sleep(0)
    with pytest.raises(ValueError, match="already running for SurfaceSlice"):
        organizer.queue_solver("surface-one")
    second = organizer.queue_solver("surface-two")
    await asyncio.sleep(0)
    assert organizer.running

    organizer.kill()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert target.hunt_state is HuntState.STOPPED
    assert target.findings == []


@pytest.mark.asyncio
async def test_live_role_branches_use_typed_adapters(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    import bountyhunter.organizer.service as service

    session = _session(tmp_path)
    session.openai.use_session_key("sk-test")
    target = Target(
        name="LiveGraph",
        authorized=True,
        in_scope=["live.test"],
        notes="Log in with the authorized test account.",
    )
    for config in target.agent_config.values():
        config.use_llm = True
    session.new_unsaved(target)
    session.save_as(tmp_path / "live-graph.bountyhunt")

    async def fake_brief(_deps):
        return SimpleNamespace(summary="bounded graph", cautions=[])

    async def fake_map(_deps):
        return [
            SurfaceSlice(id="surface-one", title="One", asset="live.test", kind=SliceKind.WEB),
            SurfaceSlice(id="surface-two", title="Two", asset="live.test", kind=SliceKind.API),
        ]

    async def fake_solver(_deps, surface, hypothesis=None):
        return Finding(
            id=f"finding-{surface.id}",
            title=f"Candidate {surface.title}",
            severity=Severity.LOW,
            slice_id=surface.id,
            summary="operator evidence",
        )

    async def fake_oracle(_deps, *, seen_keys):
        # Live Oracle branch: converge immediately with no new directives, so the
        # baseline two findings / two reports are unchanged.
        return SimpleNamespace(
            iteration_summary="live",
            hypotheses=[],
            chains=[],
            escalations=[],
            converged=True,
            stop_reason="done",
        )

    async def fake_dedup(_deps, _finding, _titles):
        return SimpleNamespace(risk="low", rationale="distinct locally")

    async def fake_advocate(_deps, _finding):
        return SimpleNamespace(pass_gate=True, verdict="pass", reason="")

    async def fake_evidence(_deps, _finding):
        return SimpleNamespace(
            reproduction_steps=["Log in with the authorized test account."],
            artifact_paths=[],
            missing_artifacts=["redacted request and response"],
        )

    async def fake_report(_deps, _finding):
        return SimpleNamespace(
            summary="Live summary",
            impact="Live impact",
            operator_verification_note="Insert verified operator notes.",
        )

    monkeypatch.setattr(service, "organizer_brief", fake_brief)
    monkeypatch.setattr(service, "map_target", fake_map)
    monkeypatch.setattr(service, "oracle_plan", fake_oracle)
    monkeypatch.setattr(service, "assess_slice", fake_solver)
    monkeypatch.setattr(service, "assess_duplicate", fake_dedup)
    monkeypatch.setattr(service, "advocate_review", fake_advocate)
    monkeypatch.setattr(service, "evidence_review", fake_evidence)
    monkeypatch.setattr(service, "reporter_narrative", fake_report)

    organizer = Organizer(session)
    organizer.start()
    assert organizer.task is not None
    await organizer.task
    assert target.hunt_state is HuntState.COMPLETE
    assert len(target.slices) == 2
    assert len(target.findings) == 2
    assert len(target.reports) == 2
    assert "Live summary" in target.reports[0].markdown
    assert "1. Log in with the authorized test account." in target.reports[0].markdown
    reproduction = session.path / "evidence" / "finding-surface-one-reproduction.md"
    assert reproduction.is_file()
    assert "Log in with the authorized test account." in reproduction.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_oracle_loop_tests_hypotheses_and_records_chains(tmp_path: Path, monkeypatch) -> None:
    import bountyhunter.organizer.service as service
    from bountyhunter.agents.live import (
        ChainProposal,
        EscalationProposal,
        HypothesisProposal,
        OraclePlan,
    )
    from bountyhunter.models import DirectiveStatus, WeaknessClass

    session = _session(tmp_path)
    session.openai.use_session_key("sk-test")
    target = Target(name="OracleLoop", authorized=True, in_scope=["live.test"])
    # Only the Oracle and the Solver run live; Mapping and the review gates stay stub.
    target.agent_config["oracle"].use_llm = True
    target.agent_config["solver"].use_llm = True
    session.new_unsaved(target)

    async def fake_solver(_deps, surface, hypothesis=None):
        if hypothesis is None:
            return Finding(
                id=f"finding-{surface.id}",
                title=f"Candidate {surface.title}",
                severity=Severity.MEDIUM,
                slice_id=surface.id,
                summary="operator evidence",
            )
        return Finding(
            id=f"finding-{hypothesis.id}",
            title=f"Hypothesis {hypothesis.id} confirmed",
            severity=Severity.HIGH,
            slice_id=surface.id,
            hypothesis_id=hypothesis.id,
            summary="operator evidence for the hypothesis",
        )

    async def fake_hypothesis_solver(_deps, surface, hypotheses):
        return {
            hypothesis.id: Finding(
                id=f"finding-{hypothesis.id}",
                title=f"Hypothesis {hypothesis.id} confirmed",
                severity=Severity.HIGH,
                slice_id=surface.id,
                hypothesis_id=hypothesis.id,
                summary="operator evidence for the hypothesis",
            )
            for hypothesis in hypotheses
        }

    rounds = {"n": 0}

    async def fake_oracle(_deps, *, seen_keys):
        rounds["n"] += 1
        if rounds["n"] == 1:
            return OraclePlan(
                iteration_summary="round 1",
                hypotheses=[
                    HypothesisProposal(
                        slice_id="surface-account-settings",
                        weakness=WeaknessClass.IDOR,
                        statement="cross-tenant invoice read",
                        test="swap the account id",
                        expected_signal="200 with another tenant's invoice",
                    )
                ],
                chains=[
                    ChainProposal(
                        finding_ids=[
                            "finding-surface-account-settings",
                            "finding-surface-invoice-export",
                        ],
                        combined_severity=Severity.HIGH,
                        impact="Full account takeover",
                        demonstration="combine the export boundary with the isolation gap",
                    )
                ],
                escalations=[
                    EscalationProposal(
                        finding_id="finding-surface-account-settings",
                        from_severity=Severity.MEDIUM,
                        to_severity=Severity.HIGH,
                        probe="pivot the export boundary to another tenant",
                        impact="cross-tenant data exposure",
                    )
                ],
            )
        return OraclePlan(iteration_summary="done", converged=True, stop_reason="dry")

    monkeypatch.setattr(service, "assess_slice", fake_solver)
    monkeypatch.setattr(service, "assess_hypotheses", fake_hypothesis_solver)
    monkeypatch.setattr(service, "oracle_plan", fake_oracle)

    organizer = Organizer(session)
    organizer.start()
    assert organizer.task is not None
    await organizer.task

    assert target.hunt_state is HuntState.COMPLETE
    # One hypothesis proposed, tested by a focused Solver, and confirmed.
    assert len(target.hypotheses) == 1
    hyp = target.hypotheses[0]
    assert hyp.id == "hyp-001"
    assert hyp.status is DirectiveStatus.CONFIRMED
    assert hyp.finding_id == "finding-hyp-001"
    # The hypothesis-driven finding carries lineage back to its hypothesis.
    hyp_finding = next(f for f in target.findings if f.id == "finding-hyp-001")
    assert hyp_finding.hypothesis_id == "hyp-001"
    # Two baseline findings + one hypothesis finding.
    assert len(target.findings) == 3
    # Chain and escalation are recorded as first-class directives.
    assert len(target.chains) == 1
    assert target.chains[0].finding_ids == [
        "finding-surface-account-settings",
        "finding-surface-invoice-export",
    ]
    assert len(target.escalations) == 1
    assert target.escalations[0].finding_id == "finding-surface-account-settings"
    # fake_oracle was called until it converged (round 1 work + round 2 converge).
    assert rounds["n"] == 2


def test_organizer_start_authorized(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.new_unsaved(Target(name="Good", authorized=True, in_scope=["g.test"]))
    Organizer(session).start()
    assert session.target is not None
    assert session.target.hunt_state is HuntState.MAPPING
