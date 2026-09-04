"""Unit tests for the pure Oracle keying + proposal-absorption helpers."""

from __future__ import annotations

from bountyhunter.agents.live import (
    ChainProposal,
    EscalationProposal,
    HypothesisProposal,
    OraclePlan,
)
from bountyhunter.models import (
    DirectiveStatus,
    Finding,
    Severity,
    SurfaceSlice,
    Target,
    WeaknessClass,
)
from bountyhunter.organizer.oracle import (
    absorb_plan,
    chain_key,
    escalation_key,
    hypothesis_key,
    stub_oracle_plan,
)


def _target() -> Target:
    return Target(
        name="t",
        in_scope=["app.example.com"],
        findings=[Finding(id="finding-a", title="A"), Finding(id="finding-b", title="B")],
    )


def test_stub_plan_is_empty_and_converged() -> None:
    plan = stub_oracle_plan()
    assert plan.converged
    assert not plan.hypotheses and not plan.chains and not plan.escalations


def test_keys_are_deterministic_and_order_independent() -> None:
    hyp = HypothesisProposal(
        slice_id="s1",
        weakness=WeaknessClass.IDOR,
        statement="Read others' data!",
        test="t",
        expected_signal="e",
    )
    assert hypothesis_key(hyp) == hypothesis_key(hyp)
    assert chain_key(ChainProposal(finding_ids=["b", "a"], impact="i", demonstration="d")) == "c:a+b"
    assert (
        escalation_key(
            EscalationProposal(finding_id="finding-a", to_severity=Severity.CRITICAL, probe="p", impact="i")
        )
        == "e:finding-a:critical"
    )


def test_absorb_assigns_ids_keys_and_persists() -> None:
    target = _target()
    plan = OraclePlan(
        iteration_summary="x",
        hypotheses=[
            HypothesisProposal(
                slice_id="surface-1",
                weakness=WeaknessClass.IDOR,
                statement="user can read others invoices",
                test="compare ids",
                expected_signal="200 with another user's data",
            )
        ],
    )
    seen: set[str] = set()
    fresh = absorb_plan(target, plan, seen)
    assert len(fresh.hypotheses) == 1
    hyp = fresh.hypotheses[0]
    assert hyp.id == "hyp-001"
    assert hyp.dedupe_key in seen
    assert hyp.status is DirectiveStatus.PROPOSED
    assert target.hypotheses == fresh.hypotheses  # persisted onto the Target


def test_absorb_dedupes_against_seen_across_rounds() -> None:
    target = _target()
    proposal = HypothesisProposal(
        slice_id="s1",
        weakness=WeaknessClass.SSRF,
        statement="same",
        test="t",
        expected_signal="e",
    )
    seen: set[str] = set()
    first = absorb_plan(target, OraclePlan(iteration_summary="x", hypotheses=[proposal]), seen)
    second = absorb_plan(target, OraclePlan(iteration_summary="x", hypotheses=[proposal]), seen)
    assert len(first.hypotheses) == 1
    assert len(second.hypotheses) == 0
    assert len(target.hypotheses) == 1


def test_chain_requires_all_known_findings() -> None:
    target = _target()
    plan = OraclePlan(
        iteration_summary="x",
        chains=[
            ChainProposal(finding_ids=["finding-a", "finding-b"], impact="ATO", demonstration="d"),
            ChainProposal(finding_ids=["finding-a", "finding-x"], impact="?", demonstration="d"),
        ],
    )
    fresh = absorb_plan(target, plan, set())
    assert len(fresh.chains) == 1
    assert fresh.chains[0].finding_ids == ["finding-a", "finding-b"]
    assert fresh.chains[0].id == "chain-001"


def test_escalation_requires_known_finding() -> None:
    target = _target()
    plan = OraclePlan(
        iteration_summary="x",
        escalations=[
            EscalationProposal(finding_id="finding-a", to_severity=Severity.HIGH, probe="p", impact="i"),
            EscalationProposal(finding_id="finding-z", to_severity=Severity.HIGH, probe="p", impact="i"),
        ],
    )
    fresh = absorb_plan(target, plan, set())
    assert len(fresh.escalations) == 1
    assert fresh.escalations[0].finding_id == "finding-a"


def test_escalation_must_raise_persisted_finding_severity() -> None:
    target = Target(
        name="severity",
        in_scope=["app.example.com"],
        findings=[Finding(id="finding-a", title="A", severity=Severity.HIGH)],
    )
    plan = OraclePlan(
        iteration_summary="x",
        escalations=[
            EscalationProposal(
                finding_id="finding-a",
                from_severity=Severity.NONE,
                to_severity=Severity.MEDIUM,
                probe="p",
                impact="not actually higher",
            ),
            EscalationProposal(
                finding_id="finding-a",
                from_severity=Severity.NONE,
                to_severity=Severity.CRITICAL,
                probe="p",
                impact="higher",
            ),
        ],
    )
    fresh = absorb_plan(target, plan, set())
    assert len(fresh.escalations) == 1
    assert fresh.escalations[0].from_severity is Severity.HIGH
    assert fresh.escalations[0].to_severity is Severity.CRITICAL


async def test_oracle_hypotheses_use_one_concurrent_solver_per_slice(
    tmp_path, monkeypatch
) -> None:
    import asyncio

    import bountyhunter.organizer.service as service
    from bountyhunter.events import EventBus
    from bountyhunter.organizer.service import HuntDeps, KillSwitch
    from bountyhunter.session import Session
    from bountyhunter.settings import AppSettings

    target = Target(
        name="parallel",
        authorized=True,
        in_scope=["a.test", "b.test"],
        slices=[
            SurfaceSlice(id="surface-a", title="A", asset="a.test"),
            SurfaceSlice(id="surface-b", title="B", asset="b.test"),
        ],
    )
    session = Session(settings=AppSettings(), settings_path=tmp_path / "settings.json")
    session.new_unsaved(target)
    deps = HuntDeps(
        session=session,
        target=target,
        event_bus=EventBus(),
        kill_switch=KillSwitch(),
        hunt_id="parallel-test",
    )
    hypotheses = [
        service.Hypothesis(
            id=f"hyp-{index}",
            dedupe_key=f"h:{index}",
            slice_id=slice_id,
            statement=f"statement {index}",
            test="bounded check",
            expected_signal="operator evidence",
        )
        for index, slice_id in enumerate(("surface-a", "surface-a", "surface-b"), start=1)
    ]
    active_by_slice: dict[str, int] = {}
    max_by_slice: dict[str, int] = {}
    active_global = 0
    max_global = 0

    calls_by_slice: dict[str, int] = {}

    async def fake_assess(_deps, surface, slice_hypotheses):
        nonlocal active_global, max_global
        calls_by_slice[surface.id] = calls_by_slice.get(surface.id, 0) + 1
        active_by_slice[surface.id] = active_by_slice.get(surface.id, 0) + 1
        max_by_slice[surface.id] = max(
            max_by_slice.get(surface.id, 0), active_by_slice[surface.id]
        )
        active_global += 1
        max_global = max(max_global, active_global)
        await asyncio.sleep(0.02)
        active_global -= 1
        active_by_slice[surface.id] -= 1
        return {
            hypothesis.id: Finding(
                id=f"finding-{hypothesis.id}",
                title=hypothesis.statement,
                slice_id=surface.id,
                hypothesis_id=hypothesis.id,
            )
            for hypothesis in slice_hypotheses
        }

    monkeypatch.setattr(service, "assess_hypotheses", fake_assess)
    findings = await service._test_hypotheses(deps, hypotheses, solver_live=True)

    assert len(findings) == 3
    assert max_by_slice == {"surface-a": 1, "surface-b": 1}
    assert calls_by_slice == {"surface-a": 1, "surface-b": 1}
    assert max_global == 2  # distinct slices still fan out in parallel
