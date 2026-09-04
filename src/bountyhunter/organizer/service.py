"""Async Organizer graph for local stubs and opt-in hosted role adapters."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from uuid import uuid4

from bountyhunter.agents.factory import ModelConfigurationError
from bountyhunter.agents.live import (
    advocate_review,
    assess_duplicate,
    assess_hypotheses,
    assess_slice,
    evidence_review,
    map_target,
    oracle_plan,
    organizer_brief,
    reporter_narrative,
)
from bountyhunter.agents.reporter import ReporterAdapter
from bountyhunter.events import EventBus
from bountyhunter.models import (
    AgentRole,
    DirectiveStatus,
    EventKind,
    EventLevel,
    Finding,
    FindingStatus,
    HuntState,
    Hypothesis,
    ModelProvider,
    ReportDraft,
    Severity,
    SliceKind,
    SurfaceSlice,
    Target,
)
from bountyhunter.organizer.oracle import absorb_plan, stub_oracle_plan
from bountyhunter.safety import ScopeGuard
from bountyhunter.session import Session
from bountyhunter.store import HuntStore

_PROVIDER_READINESS_HELP: dict[ModelProvider, str] = {
    ModelProvider.OPENAI: (
        "neither a ChatGPT-backed Codex login nor an API key is available. Run `codex login`, "
        "use Target → OpenAI connection…, or set OPENAI_API_KEY."
    ),
    ModelProvider.ANTHROPIC: (
        "neither a Claude subscription login nor an API key is available. Run "
        "`claude auth login`, use Target → Anthropic connection…, or set ANTHROPIC_API_KEY."
    ),
}


def require_live_models_ready(session: Session, target: Target) -> None:
    require_live_models_ready_for(session, target)


def require_live_models_ready_for(
    session: Session,
    target: Target,
    roles: set[AgentRole] | None = None,
) -> None:
    """Validate credentials for the live roles that an operation will actually use."""
    live_by_provider: dict[ModelProvider, list[str]] = {}
    for config in target.agent_config.values():
        if roles is not None and config.role not in roles:
            continue
        if not config.is_live:
            continue
        if not config.model.strip():
            raise ModelConfigurationError(f"No model selected for {config.role.value}.")
        if config.provider is ModelProvider.LOCAL:
            continue
        live_by_provider.setdefault(config.provider, []).append(config.role.value)

    for provider, roles in live_by_provider.items():
        connection = session.connection_for(provider)
        subscription_ready = False
        if connection.use_subscription_first:
            subscription_ready = connection.refresh_subscription()
        if not subscription_ready and not connection.configured:
            names = ", ".join(sorted(roles))
            provider_label = provider.value.title()
            help_text = _PROVIDER_READINESS_HELP.get(
                provider, "no credentials are available."
            )
            raise ModelConfigurationError(
                f"{provider_label} is enabled for {names}, but {help_text}"
            )


class HuntKilled(asyncio.CancelledError):
    """Raised at an Organizer checkpoint after the operator presses Kill."""


class HuntTimeLimitReached(RuntimeError):
    """Raised when a Target's optional continuous-hunt timer expires."""


class KillSwitch:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.reason = "operator"

    @property
    def killed(self) -> bool:
        return self._event.is_set()

    def kill(self, reason: str = "operator") -> None:
        self.reason = reason
        self._event.set()

    def reset(self) -> None:
        self._event = asyncio.Event()
        self.reason = "operator"

    def checkpoint(self) -> None:
        if self.killed:
            raise HuntKilled()


@dataclass(slots=True)
class HuntDeps:
    """Dependencies available to agents for exactly one open Target."""

    session: Session
    target: Target
    event_bus: EventBus
    kill_switch: KillSwitch
    hunt_id: str
    pause_gate: asyncio.Event = field(default_factory=asyncio.Event)
    resume_state: HuntState = HuntState.HUNTING
    deadline: float | None = None
    in_scope_snapshot: tuple[str, ...] = field(init=False)
    out_of_scope_snapshot: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        self.pause_gate.set()
        self.in_scope_snapshot = tuple(self.target.in_scope)
        self.out_of_scope_snapshot = tuple(self.target.out_of_scope)

    async def checkpoint(self) -> None:
        self.kill_switch.checkpoint()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise HuntTimeLimitReached()
        if self.deadline is None:
            await self.pause_gate.wait()
        else:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise HuntTimeLimitReached()
            try:
                await asyncio.wait_for(self.pause_gate.wait(), timeout=remaining)
            except TimeoutError as exc:
                raise HuntTimeLimitReached() from exc
        self.kill_switch.checkpoint()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise HuntTimeLimitReached()
        if self.session.target is None or self.session.target.id != self.target.id:
            raise HuntKilled()
        ScopeGuard.require_huntable(self.target)
        if (
            tuple(self.target.in_scope) != self.in_scope_snapshot
            or tuple(self.target.out_of_scope) != self.out_of_scope_snapshot
        ):
            raise ValueError("Scope changed while work was running; restart the hunt.")

    def emit(
        self,
        message: str,
        *,
        source: str,
        kind: EventKind = EventKind.TASK,
        level: EventLevel = EventLevel.AGENT,
        slice_id: str | None = None,
    ) -> None:
        if self.session.target is None or self.session.target.id != self.target.id:
            return
        self.session.log(
            message,
            source=source,
            level=level,
            kind=kind,
            hunt_id=self.hunt_id,
            slice_id=slice_id,
        )


def _example_slices(target: Target) -> list[SurfaceSlice]:
    """Bounded local example output; this function performs no recon or I/O."""
    primary = target.in_scope[0]
    secondary = target.in_scope[1] if len(target.in_scope) > 1 else primary
    return [
        SurfaceSlice(
            id="surface-account-settings",
            title="Account settings surface",
            asset=primary,
            kind=SliceKind.WEB,
            notes="Stub slice derived only from the declared scope.",
        ),
        SurfaceSlice(
            id="surface-invoice-export",
            title="Invoice export surface",
            asset=secondary,
            kind=SliceKind.API,
            notes="Separate bounded slice; it receives a separate Solver.",
        ),
    ]


async def _solver_for_slice(deps: HuntDeps, surface: SurfaceSlice) -> Finding | None:
    # This guard is deliberately immediately before each queued unit of work.
    ScopeGuard.require_slice(deps.target, surface)
    await deps.checkpoint()
    task_id = f"solver-{surface.id}-{uuid4().hex[:6]}"
    surface.solver_task_id = task_id
    surface.status = "running"
    deps.emit(
        f"Solver queued for one slice: {surface.title}",
        source="solver",
        slice_id=surface.id,
    )
    await asyncio.sleep(0.08)
    await deps.checkpoint()
    surface.status = "review"
    config = deps.target.agent_config[AgentRole.SOLVER.value]
    if not config.enabled:
        surface.status = "skipped"
        deps.emit("Solver role is disabled; slice skipped", source="solver", slice_id=surface.id)
        return None
    if config.is_live:
        finding = await assess_slice(deps, surface)
        await deps.checkpoint()
        if finding is None:
            surface.status = "no-candidate"
            deps.emit(
                "Live Solver found no evidence-backed candidate",
                source="solver",
                kind=EventKind.RESULT,
                slice_id=surface.id,
            )
            return None
    elif surface.id == "surface-account-settings":
        finding = Finding(
            id="finding-account-export-boundary",
            title="Account export authorization boundary needs review",
            severity=Severity.MEDIUM,
            slice_id=surface.id,
            summary=(
                "Stub candidate produced from local example data only; validate manually "
                "inside the program policy before reporting."
            ),
        )
    else:
        finding = Finding(
            id="finding-invoice-export-unverified",
            title="Invoice export isolation concern is unverified",
            severity=Severity.LOW,
            slice_id=surface.id,
            summary="Stub candidate with intentionally insufficient supporting evidence.",
        )
    deps.emit(
        f"Solver returned candidate {finding.id}",
        source="solver",
        kind=EventKind.RESULT,
        slice_id=surface.id,
    )
    return finding


def render_report(target: Target, finding: Finding) -> str:
    """Compatibility helper around the export-only Reporter adapter."""
    return ReporterAdapter().render(target, finding)


ORACLE_MAX_ROUNDS = 6
ORACLE_DRY_ROUNDS = 2


def _reset_hunt_outputs(
    target: Target, *, keep_slices: bool, preserve_results: bool = False
) -> None:
    """Start a graph pass with one coherent set of derived artifacts.

    A previous implementation replaced findings but retained old Oracle
    directives, which allowed stale chains and duplicate hypotheses to leak into
    a later run. A first/one-pass run clears all derived output. A later
    continuous pass deliberately retains prior results and directives, while
    existing slices are retained only when Mapping is disabled.
    """
    if keep_slices:
        for surface in target.slices:
            surface.status = "pending"
            surface.solver_task_id = None
    else:
        target.slices = []
    if not preserve_results:
        target.findings = []
        target.hypotheses = []
        target.chains = []
        target.escalations = []
        target.reports = []


def _resolve_slice_for_hypothesis(deps: HuntDeps, hyp: Hypothesis) -> SurfaceSlice | None:
    """Map a hypothesis onto exactly one in-scope slice, or None if unresolvable.

    A hypothesis that names a fresh in-scope asset mints a bounded slice so the
    Solver still stays narrow. ScopeGuard.require_slice re-validates before any
    Solver runs, so even a minted slice is gated deterministically.
    """
    target = deps.target
    if hyp.slice_id:
        surface = next((s for s in target.slices if s.id == hyp.slice_id), None)
        if surface is None:
            deps.emit(
                f"Hypothesis {hyp.id} names unknown slice {hyp.slice_id}; skipped",
                source="oracle",
            )
        return surface
    asset = hyp.proposed_asset.strip()
    if asset and asset in target.in_scope:
        minted = sum(1 for s in target.slices if s.id.startswith("surface-oracle-"))
        surface = SurfaceSlice(
            id=f"surface-oracle-{minted + 1}",
            title=(hyp.statement.strip()[:60] or asset),
            asset=asset,
            kind=SliceKind.OTHER,
            notes="Oracle-directed area; asset is already in the declared scope.",
        )
        target.slices.append(surface)
        return surface
    deps.emit(f"Hypothesis {hyp.id} has no in-scope target; skipped", source="oracle")
    return None


async def _test_hypotheses(
    deps: HuntDeps, hypotheses: list[Hypothesis], *, solver_live: bool
) -> list[Finding]:
    """Run one concurrent Solver worker per slice; return confirmed findings."""
    if not hypotheses:
        return []
    if not solver_live:
        for hyp in hypotheses:
            hyp.status = DirectiveStatus.DEFERRED
        deps.emit(
            "Fresh hypotheses need a live Solver to test; deferred for the operator",
            source="oracle",
        )
        return []
    # Several hypotheses may target the same slice. Keep exactly one Solver turn
    # per slice by batching them; turns for different slices still fan out.
    scheduled: dict[str, tuple[SurfaceSlice, list[Hypothesis]]] = {}
    for hyp in hypotheses:
        surface = _resolve_slice_for_hypothesis(deps, hyp)
        if surface is None:
            hyp.status = DirectiveStatus.ABANDONED
            continue
        hyp.status = DirectiveStatus.QUEUED
        if surface.id not in scheduled:
            scheduled[surface.id] = (surface, [])
        scheduled[surface.id][1].append(hyp)
    if not scheduled:
        return []

    async def solve_slice_batch(
        surface: SurfaceSlice, slice_hypotheses: list[Hypothesis]
    ) -> list[Finding]:
        # One model turn receives one slice and every fresh hypothesis for that
        # slice. This is an actual one-agent-per-slice boundary, not merely a
        # concurrency semaphore around several independent agents.
        ScopeGuard.require_slice(deps.target, surface)
        await deps.checkpoint()
        surface.solver_task_id = f"solver-{surface.id}-{uuid4().hex[:6]}"
        surface.status = "running"
        deps.emit(
            f"Solver testing {len(slice_hypotheses)} hypotheses on one slice: {surface.title}",
            source="solver",
            slice_id=surface.id,
        )
        await asyncio.sleep(0.08)
        await deps.checkpoint()
        by_hypothesis = await assess_hypotheses(deps, surface, slice_hypotheses)
        await deps.checkpoint()
        for hyp in slice_hypotheses:
            finding = by_hypothesis.get(hyp.id)
            if finding is None:
                hyp.status = DirectiveStatus.REFUTED
                deps.emit(
                    f"Hypothesis {hyp.id} refuted: no evidence-backed candidate",
                    source="solver",
                    kind=EventKind.RESULT,
                    slice_id=surface.id,
                )
                continue
            hyp.status = DirectiveStatus.CONFIRMED
            hyp.finding_id = finding.id
            deps.emit(
                f"Hypothesis {hyp.id} confirmed -> {finding.id}",
                source="solver",
                kind=EventKind.RESULT,
                slice_id=surface.id,
            )
        surface.status = "review" if by_hypothesis else "no-candidate"
        return list(by_hypothesis.values())

    deps.session.gui.solvers_alive += len(scheduled)
    deps.session.notify()
    try:
        tasks = [
            asyncio.create_task(
                solve_slice_batch(surface, slice_hypotheses), name=f"solver:{surface.id}:oracle"
            )
            for surface, slice_hypotheses in scheduled.values()
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        deps.session.gui.solvers_alive = max(
            0, deps.session.gui.solvers_alive - len(scheduled)
        )
        deps.session.notify()
    findings: list[Finding] = []
    for result in raw_results:
        if isinstance(result, BaseException):
            raise result
        findings.extend(result)
    return findings


async def _run_oracle_loop(deps: HuntDeps) -> None:
    """Iterative strategist: propose -> schedule -> collect -> re-evaluate.

    No-op unless the Oracle role is enabled. In stub mode it runs one converged
    round with no proposals and never fabricates strategy work.
    """
    target = deps.target
    oracle_config = target.agent_config[AgentRole.ORACLE.value]
    if not oracle_config.enabled:
        return
    live = oracle_config.is_live
    solver_config = target.agent_config[AgentRole.SOLVER.value]
    solver_live = solver_config.is_live

    deps.session.hunt_note = "Oracle proposing hypotheses, chains, and escalations"
    deps.emit(f"Oracle loop started ({'live' if live else 'local stub'})", source="oracle")
    # Continuous cycles retain prior directives. Seeding the set prevents the
    # strategist from scheduling the same semantic proposal again.
    seen: set[str] = {
        item.dedupe_key
        for collection in (target.hypotheses, target.chains, target.escalations)
        for item in collection
    }
    dry = 0
    for round_no in range(1, ORACLE_MAX_ROUNDS + 1):
        await deps.checkpoint()
        plan = await oracle_plan(deps, seen_keys=sorted(seen)) if live else stub_oracle_plan()
        await deps.checkpoint()
        fresh = absorb_plan(target, plan, seen)
        deps.emit(
            f"Oracle round {round_no}: {len(fresh.hypotheses)} hypotheses, "
            f"{len(fresh.chains)} chains, {len(fresh.escalations)} escalations — "
            f"{plan.iteration_summary}",
            source="oracle",
            kind=EventKind.RESULT,
        )
        new_findings = await _test_hypotheses(deps, fresh.hypotheses, solver_live=solver_live)
        target.findings.extend(new_findings)
        for chain in fresh.chains:
            deps.emit(
                f"Chain {chain.id} ({chain.combined_severity.value}): {chain.impact}",
                source="oracle",
                kind=EventKind.RESULT,
            )
        for esc in fresh.escalations:
            deps.emit(
                f"Escalation {esc.id} on {esc.finding_id} "
                f"({esc.from_severity.value}->{esc.to_severity.value}): {esc.impact}",
                source="oracle",
                kind=EventKind.RESULT,
            )
        deps.session.notify()
        if plan.converged:
            deps.emit(
                f"Oracle converged: {plan.stop_reason or 'no further productive work'}",
                source="oracle",
            )
            break
        produced = len(fresh.hypotheses) + len(fresh.chains) + len(fresh.escalations)
        if produced == 0:
            dry += 1
            deps.emit(f"Oracle dry round {dry}/{ORACLE_DRY_ROUNDS}", source="oracle")
            if dry >= ORACLE_DRY_ROUNDS:
                break
        else:
            dry = 0


async def _run_hunt_once(deps: HuntDeps, *, preserve_results: bool = False) -> bool:
    """Run one complete configured graph pass."""
    target = ScopeGuard.require_huntable(deps.target)
    try:
        organizer_config = target.agent_config[AgentRole.ORGANIZER.value]
        if organizer_config.is_live:
            brief = await organizer_brief(deps)
            await deps.checkpoint()
            cautions = "; ".join(brief.cautions) if brief.cautions else "none"
            deps.emit(
                f"Organizer brief: {brief.summary} Cautions: {cautions}",
                source="organizer",
                kind=EventKind.RESULT,
            )
        mapping_config = target.agent_config[AgentRole.MAPPING.value]
        _reset_hunt_outputs(
            target,
            keep_slices=not mapping_config.enabled,
            preserve_results=preserve_results,
        )
        target.hunt_state = HuntState.MAPPING
        deps.session.hunt_note = "Mapping declared scope into bounded slices"
        mapping_mode = (
            f"{mapping_config.provider.value} live" if mapping_config.is_live else "local stub"
        )
        deps.emit(f"Mapping started from declared scope ({mapping_mode})", source="mapping")
        await asyncio.sleep(0.08)
        await deps.checkpoint()

        if not mapping_config.enabled:
            if not target.slices:
                raise ValueError("Mapping is disabled and the Target has no existing slices.")
        elif mapping_config.is_live:
            target.slices = await map_target(deps)
            await deps.checkpoint()
        else:
            target.slices = _example_slices(target)
        slice_ids = [surface.id for surface in target.slices]
        if len(slice_ids) != len(set(slice_ids)):
            raise ValueError("Mapping produced duplicate SurfaceSlice ids.")
        deps.emit(
            f"Mapping created {len(target.slices)} bounded SurfaceSlices",
            source="mapping",
            kind=EventKind.RESULT,
        )
        deps.session.notify()

        target.hunt_state = HuntState.HUNTING
        deps.session.hunt_note = "One Solver is running per SurfaceSlice"
        # Never give one Solver multiple slices; each coroutine closes over one.
        solver_config = target.agent_config[AgentRole.SOLVER.value]
        if not solver_config.enabled:
            for surface in target.slices:
                surface.status = "skipped"
            raw_results: list[Finding | None | BaseException] = []
            deps.emit("Solver role disabled; baseline slice sweep skipped", source="solver")
        else:
            deps.session.gui.solvers_alive = len(target.slices)
            solver_tasks = [
                asyncio.create_task(
                    _solver_for_slice(deps, surface), name=f"solver:{surface.id}"
                )
                for surface in target.slices
            ]
            deps.session.notify()
            try:
                raw_results = await asyncio.gather(*solver_tasks, return_exceptions=True)
            finally:
                deps.session.gui.solvers_alive = 0
                deps.session.notify()
        for result in raw_results:
            if isinstance(result, BaseException):
                raise result
        results = [result for result in raw_results if isinstance(result, Finding)]
        deps.kill_switch.checkpoint()
        target.findings = (
            _merge_artifacts(target.findings, results, key="id")
            if preserve_results
            else results
        )

        # Strategist loop: the Oracle proposes; the Organizer schedules under gates.
        await _run_oracle_loop(deps)

        target.hunt_state = HuntState.REVIEWING
        deps.session.hunt_note = "Dedup and Devil’s advocate gates"
        dedup_config = target.agent_config[AgentRole.DEDUP.value]
        if dedup_config.enabled:
            deps.emit("Dedup gate checking candidates", source="dedup")
            await asyncio.sleep(0.06)
            for finding in target.findings:
                await deps.checkpoint()
                if dedup_config.is_live:
                    review = await assess_duplicate(
                        deps,
                        finding,
                        [other.title for other in target.findings if other.id != finding.id],
                    )
                    await deps.checkpoint()
                    finding.duplicate_risk = f"{review.risk} — {review.rationale}"
                else:
                    finding.duplicate_risk = "low (stub)"
        else:
            deps.emit("Dedup role disabled; gate skipped", source="dedup")

        devil_config = target.agent_config[AgentRole.DEVIL.value]
        if devil_config.enabled:
            deps.emit("Devil’s advocate gate reviewing report quality", source="devil")
            await asyncio.sleep(0.06)
            await deps.checkpoint()
            for finding in target.findings:
                await deps.checkpoint()
                if devil_config.is_live:
                    verdict = await advocate_review(deps, finding)
                    await deps.checkpoint()
                    finding.advocate_verdict = verdict.verdict
                    if (
                        verdict.pass_gate
                        and finding.severity is not Severity.NONE
                        and bool(finding.summary.strip())
                    ):
                        finding.status = FindingStatus.REPORTABLE
                    else:
                        finding.status = FindingStatus.KILLED
                        finding.killed_reason = verdict.reason or (
                            "Candidate has no defensible severity or factual summary."
                        )
                elif finding.id.endswith("unverified"):
                    finding.status = FindingStatus.KILLED
                    finding.advocate_verdict = "kill — insufficient evidence"
                    finding.killed_reason = "Insufficient evidence in the local stub result."
                else:
                    # Local Solver output is demonstrative, not operator evidence.
                    # A stub must never promote its own fabricated candidate.
                    finding.status = FindingStatus.KILLED
                    finding.advocate_verdict = "kill — local stub is not verified evidence"
                    finding.killed_reason = (
                        "Local example output cannot pass the report-quality gate."
                    )
            passed = sum(f.status is FindingStatus.REPORTABLE for f in target.findings)
            killed = sum(f.status is FindingStatus.KILLED for f in target.findings)
            deps.emit(
                f"Advocate passed {passed} and killed {killed} weak candidates",
                source="devil",
                kind=EventKind.RESULT,
            )
        else:
            deps.emit("Devil’s advocate disabled; no candidate passed the gate", source="devil")

        gate_passed = [f for f in target.findings if f.status is FindingStatus.REPORTABLE]
        evidence_config = target.agent_config[AgentRole.EVIDENCE.value]
        if evidence_config.enabled:
            for finding in gate_passed:
                await deps.checkpoint()
                if evidence_config.is_live:
                    package = await evidence_review(deps, finding)
                    await deps.checkpoint()
                    # Evidence paths must already occur in operator-supplied data;
                    # this prevents a model from inventing screenshot/video files.
                    supplied = "\n".join(
                        [target.notes, finding.summary, *finding.evidence]
                    )
                    finding.reproduction_steps = [
                        step for step in package.reproduction_steps if step in supplied
                    ]
                    finding.evidence = [
                        path for path in package.artifact_paths if path in supplied
                    ]
                    finding.evidence_gaps = package.missing_artifacts
                else:
                    finding.reproduction_steps = []
                    finding.evidence_gaps = [
                        "Add operator-verified reproduction steps and artifact paths."
                    ]
                if not finding.reproduction_steps:
                    finding.status = FindingStatus.NEEDS_EVIDENCE
            complete = sum(
                finding.status is FindingStatus.REPORTABLE for finding in gate_passed
            )
            deps.emit(
                f"Evidence packaged {complete}/{len(gate_passed)} gate-passed candidates; "
                "the rest need verified reproduction steps",
                source="evidence",
                kind=EventKind.RESULT,
            )
        else:
            for finding in gate_passed:
                finding.status = FindingStatus.NEEDS_EVIDENCE
                finding.evidence_gaps = ["Evidence role is disabled."]
            deps.emit(
                "Evidence role disabled; report generation is blocked until evidence is attached",
                source="evidence",
            )

        reportable = [f for f in target.findings if f.status is FindingStatus.REPORTABLE]
        if deps.session.path is not None and deps.session.target is target:
            for finding in reportable:
                reproduction_path = f"evidence/{finding.id}-reproduction.md"
                finding.evidence = [reproduction_path] + [
                    path for path in finding.evidence if path != reproduction_path
                ]

        reporter_config = target.agent_config[AgentRole.REPORTER.value]
        prior_reports = list(target.reports) if preserve_results else []
        target.reports = []
        if reporter_config.enabled:
            reporter = ReporterAdapter()
            for finding in reportable:
                await deps.checkpoint()
                if reporter_config.is_live:
                    narrative = await reporter_narrative(deps, finding)
                    await deps.checkpoint()
                    markdown = reporter.render_narrative(
                        target,
                        finding,
                        summary=narrative.summary,
                        impact=narrative.impact,
                        # The model may improve summary/impact, but reproduction
                        # always comes from the verified Evidence package.
                        verification_note=reporter.reproduction(finding),
                    )
                else:
                    markdown = reporter.render(target, finding)
                target.reports.append(
                    ReportDraft(
                        id=f"report-{finding.id}",
                        finding_id=finding.id,
                        title=finding.title,
                        platform=target.platform,
                        markdown=markdown,
                    )
                )
        if preserve_results:
            target.reports = _merge_artifacts(prior_reports, target.reports, key="id")
        deps.emit(
            f"Reporter rendered {len(target.reports)} preview; nothing was submitted",
            source="reporter",
            kind=EventKind.RESULT,
        )

        target.hunt_state = HuntState.COMPLETE
        deps.session.hunt_note = "Hunt complete — review findings and report preview"
        if deps.session.path is not None and deps.session.target is target:
            store = HuntStore(deps.session.path)
            store.save(target)
            for finding in reportable:
                evidence_path = store.root / "evidence" / f"{finding.id}-reproduction.md"
                evidence_path.write_text(
                    "# Verified reproduction\n\n"
                    + "\n".join(
                        f"{index}. {step}"
                        for index, step in enumerate(finding.reproduction_steps, start=1)
                    )
                    + "\n\n## Artifact paths\n\n"
                    + ("\n".join(f"- `{path}`" for path in finding.evidence[1:]) or "- *(none)*")
                    + "\n\n## Remaining gaps\n\n"
                    + ("\n".join(f"- {gap}" for gap in finding.evidence_gaps) or "- *(none)*")
                    + "\n",
                    encoding="utf-8",
                )
            deps.session._capture()
        deps.emit("Hunt graph complete", source="organizer", kind=EventKind.RESULT)
        deps.session.notify()
        return True
    except HuntTimeLimitReached:
        target.hunt_state = HuntState.STOPPED
        deps.session.gui.solvers_alive = 0
        deps.session.hunt_note = "Stopped because the hunt timer ended"
        deps.emit(
            "Hunt time limit reached; continuous work stopped",
            source="organizer",
            kind=EventKind.STATUS,
            level=EventLevel.INFO,
        )
        deps.session.notify()
        return False
    except (HuntKilled, asyncio.CancelledError):
        target.hunt_state = HuntState.STOPPED
        deps.session.gui.solvers_alive = 0
        if deps.kill_switch.reason == "timer":
            deps.session.hunt_note = "Stopped because the hunt timer ended"
            deps.emit(
                "Hunt time limit reached; continuous work stopped",
                source="organizer",
                kind=EventKind.STATUS,
            )
        else:
            deps.session.hunt_note = "Stopped by operator"
            deps.emit(
                "Stop control ended the hunt graph",
                source="organizer",
                kind=EventKind.SAFETY,
                level=EventLevel.WARN,
            )
        deps.session.notify()
        return False
    except RuntimeError as exc:
        # Provider/transport/model failures are commonly transient. A
        # continuous hunt must honor its contract and try a later cycle rather
        # than stopping permanently on one malformed or interrupted response.
        deps.session.gui.solvers_alive = 0
        if target.continuous_hunt:
            target.hunt_state = HuntState.HUNTING
            deps.session.hunt_note = f"Model cycle failed; retry scheduled: {exc}"
            deps.emit(
                f"Hunt cycle model error; another cycle will retry: {exc}",
                source="organizer",
                kind=EventKind.ERROR,
                level=EventLevel.ERROR,
            )
        else:
            target.hunt_state = HuntState.STOPPED
            deps.session.hunt_note = f"Hunt stopped: {exc}"
            deps.emit(
                f"Hunt graph stopped: {exc}",
                source="organizer",
                kind=EventKind.ERROR,
                level=EventLevel.ERROR,
            )
        deps.session.notify()
        return False
    except Exception as exc:
        target.hunt_state = HuntState.STOPPED
        deps.session.gui.solvers_alive = 0
        deps.session.hunt_note = f"Hunt stopped: {exc}"
        deps.emit(
            f"Hunt graph stopped: {exc}",
            source="organizer",
            kind=EventKind.ERROR,
            level=EventLevel.ERROR,
        )
        deps.session.notify()
        return False


def _merge_artifacts(previous: list, current: list, *, key: str) -> list:
    """Keep artifacts from earlier continuous cycles while replacing updates."""
    merged = {getattr(item, key): item for item in previous}
    merged.update({getattr(item, key): item for item in current})
    return list(merged.values())


async def run_hunt(deps: HuntDeps) -> None:
    """Run once or continuously without blocking the Assistant/UI.

    Continuous mode starts another full pass after a small configurable delay.
    Findings and report previews from completed passes are retained. Work stops
    only when the operator presses Stop, a configured timer expires, scope is
    changed, or an actual error occurs.
    """
    cycle = 1
    while True:
        if deps.deadline is not None and time.monotonic() >= deps.deadline:
            deps.target.hunt_state = HuntState.STOPPED
            deps.session.gui.solvers_alive = 0
            deps.session.hunt_note = "Stopped because the hunt timer ended"
            deps.emit(
                "Hunt time limit reached; continuous work stopped",
                source="organizer",
                kind=EventKind.STATUS,
            )
            deps.session.notify()
            return

        previous_findings = list(deps.target.findings) if cycle > 1 else []
        previous_reports = list(deps.target.reports) if cycle > 1 else []
        previous_hypotheses = list(deps.target.hypotheses) if cycle > 1 else []
        previous_chains = list(deps.target.chains) if cycle > 1 else []
        previous_escalations = list(deps.target.escalations) if cycle > 1 else []
        deps.session.gui.hunt_cycle = cycle
        deps.emit(f"Hunt cycle {cycle} started", source="organizer")
        cycle_succeeded = await _run_hunt_once(deps, preserve_results=cycle > 1)
        if cycle > 1:
            deps.target.findings = _merge_artifacts(
                previous_findings, deps.target.findings, key="id"
            )
            deps.target.reports = _merge_artifacts(
                previous_reports, deps.target.reports, key="id"
            )
            deps.target.hypotheses = _merge_artifacts(
                previous_hypotheses, deps.target.hypotheses, key="dedupe_key"
            )
            deps.target.chains = _merge_artifacts(
                previous_chains, deps.target.chains, key="dedupe_key"
            )
            deps.target.escalations = _merge_artifacts(
                previous_escalations, deps.target.escalations, key="dedupe_key"
            )
            if deps.session.path is not None and deps.session.target is deps.target:
                HuntStore(deps.session.path).save(deps.target)
                deps.session._capture()
            deps.session.notify()

        if deps.target.hunt_state is HuntState.STOPPED or deps.kill_switch.killed:
            return

        if not deps.target.continuous_hunt:
            return

        if cycle_succeeded:
            deps.emit(
                f"Hunt cycle {cycle} complete; continuing until Stop or timer",
                source="organizer",
                kind=EventKind.RESULT,
            )
        else:
            deps.emit(
                f"Hunt cycle {cycle} did not complete; retrying until Stop or timer",
                source="organizer",
                kind=EventKind.STATUS,
                level=EventLevel.WARN,
            )
        deps.target.hunt_state = HuntState.HUNTING
        delay = deps.target.hunt_cycle_delay_seconds
        outcome = "complete" if cycle_succeeded else "failed"
        deps.session.hunt_note = (
            f"Cycle {cycle} {outcome}; next pass in {delay} seconds"
        )
        deps.session.notify()
        try:
            if deps.deadline is None:
                await asyncio.sleep(delay)
            else:
                remaining = deps.deadline - time.monotonic()
                if remaining <= 0:
                    raise HuntTimeLimitReached()
                await asyncio.sleep(min(delay, remaining))
            await deps.checkpoint()
        except HuntTimeLimitReached:
            deps.target.hunt_state = HuntState.STOPPED
            deps.session.gui.solvers_alive = 0
            deps.session.hunt_note = "Stopped because the hunt timer ended"
            deps.emit(
                "Hunt time limit reached; continuous work stopped",
                source="organizer",
                kind=EventKind.STATUS,
            )
            deps.session.notify()
            return
        except asyncio.CancelledError:
            # Stop can arrive while the graph is waiting between continuous
            # cycles. Organizer.kill has already updated state and emitted the
            # operator-facing event; consume cancellation so callers can await
            # the task cleanly just as they can during an active cycle.
            if deps.target.hunt_state is not HuntState.STOPPED:
                deps.target.hunt_state = HuntState.STOPPED
                deps.session.hunt_note = "Stopped by operator"
                deps.session.notify()
            return
        except Exception as exc:
            deps.target.hunt_state = HuntState.STOPPED
            deps.session.gui.solvers_alive = 0
            deps.session.hunt_note = f"Hunt stopped: {exc}"
            deps.emit(
                f"Hunt graph stopped: {exc}",
                source="organizer",
                kind=EventKind.ERROR,
                level=EventLevel.ERROR,
            )
            deps.session.notify()
            return
        cycle += 1


class Organizer:
    """Owns the hunt graph and schedules it with ``asyncio.create_task``."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.kill_switch = KillSwitch()
        self.task: asyncio.Task[None] | None = None
        self._timer_task: asyncio.Task[None] | None = None
        self.deps: HuntDeps | None = None
        self._manual_solver_tasks: dict[str, asyncio.Task[Finding | None]] = {}

    @property
    def _hunt_running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def running(self) -> bool:
        """Whether any Organizer-owned background work is still active."""
        return self._hunt_running or any(
            not task.done() for task in self._manual_solver_tasks.values()
        )

    def start(self) -> str:
        if self.running:
            return "Hunt is already running."
        target = ScopeGuard.require_huntable(self.session.target)
        # Assistant chat is independent of the hunt graph and must not block it.
        hunt_roles = set(AgentRole) - {AgentRole.ASSISTANT}
        require_live_models_ready_for(self.session, target, hunt_roles)
        self.kill_switch.reset()
        hunt_id = uuid4().hex
        self.session.gui.hunt_id = hunt_id
        self.session.gui.hunt_cycle = 1
        self.session.set_hunt_state(HuntState.MAPPING, "Organizer scheduled the hunt graph")
        self.deps = HuntDeps(
            session=self.session,
            target=target,
            event_bus=self.session.event_bus,
            kill_switch=self.kill_switch,
            hunt_id=hunt_id,
            deadline=(
                time.monotonic() + target.hunt_time_limit_minutes * 60
                if target.hunt_time_limit_minutes
                else None
            ),
        )
        note = "Organizer scheduled Mapping and one Solver task per slice."
        self.session.log(note, source="organizer", level=EventLevel.AGENT, hunt_id=hunt_id)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # Unit/headless callers can validate the gate synchronously and call
            # ``await run_hunt(organizer.deps)`` explicitly.
            return note
        self.task = asyncio.create_task(run_hunt(self.deps), name=f"hunt:{hunt_id}")
        if target.hunt_time_limit_minutes:
            self._timer_task = asyncio.create_task(
                self._enforce_time_limit(target.hunt_time_limit_minutes * 60),
                name=f"hunt-timer:{hunt_id}",
            )
            self.task.add_done_callback(lambda _finished: self._cancel_timer())
        return note

    def _cancel_timer(self) -> None:
        if self._timer_task is not None and not self._timer_task.done():
            self._timer_task.cancel()

    async def _enforce_time_limit(self, seconds: int) -> None:
        await asyncio.sleep(seconds)
        if not self._hunt_running or self.task is None:
            return
        self.kill_switch.kill("timer")
        if self.deps is not None:
            self.deps.pause_gate.set()
        self.task.cancel()
        if self.session.target is not None:
            self.session.log(
                "Hunt timer ended; stopping background agent tasks.",
                source="organizer",
                kind=EventKind.STATUS,
            )
            self.session.set_hunt_state(
                HuntState.STOPPED, "Stopped because the hunt timer ended"
            )

    def pause(self) -> str:
        if not self._hunt_running or self.deps is None:
            return "No running hunt"
        if self.deps.pause_gate.is_set():
            self.deps.pause_gate.clear()
            if self.session.target is not None:
                self.deps.resume_state = self.session.target.hunt_state
            note = "Hunt paused."
            self.session.set_hunt_state(HuntState.PAUSED, note)
        else:
            self.deps.pause_gate.set()
            note = "Hunt resumed."
            self.session.set_hunt_state(self.deps.resume_state, note)
        self.session.log(note, source="organizer")
        return note

    def kill(self) -> str:
        if self.session.target is None:
            return "No target"
        self.kill_switch.kill("operator")
        self._cancel_timer()
        if self.deps is not None:
            self.deps.pause_gate.set()
        if self._hunt_running and self.task is not None:
            self.task.cancel()
        for task in list(self._manual_solver_tasks.values()):
            if not task.done():
                task.cancel()
        note = "Stop requested; ending background agent tasks."
        self.session.log(
            note,
            source="organizer",
            level=EventLevel.WARN,
            kind=EventKind.SAFETY,
        )
        self.session.set_hunt_state(HuntState.STOPPED, "Stopped by operator")
        return note

    # Compatibility spelling from the first prototype.
    def stop(self) -> str:
        return self.kill()

    def queue_solver(self, slice_id: str) -> asyncio.Task[Finding | None]:
        if self._hunt_running:
            raise ValueError("A hunt is already running; the Organizer owns Solver scheduling.")
        target = ScopeGuard.require_huntable(self.session.target)
        if not target.agent_config[AgentRole.SOLVER.value].enabled:
            raise ValueError("Solver role is disabled for this Target.")
        require_live_models_ready_for(self.session, target, {AgentRole.SOLVER})
        surface = next((s for s in target.slices if s.id == slice_id), None)
        if surface is None:
            raise ValueError(f"Unknown SurfaceSlice: {slice_id}")
        ScopeGuard.require_slice(target, surface)
        existing = self._manual_solver_tasks.get(slice_id)
        if existing is not None and not existing.done():
            raise ValueError(f"A Solver is already running for SurfaceSlice: {slice_id}")
        if self.kill_switch.killed:
            self.kill_switch.reset()
            self.deps = None
        if self.deps is None or self.deps.target.id != target.id:
            hunt_id = self.session.gui.hunt_id or uuid4().hex
            self.session.gui.hunt_id = hunt_id
            self.deps = HuntDeps(
                session=self.session,
                target=target,
                event_bus=self.session.event_bus,
                kill_switch=self.kill_switch,
                hunt_id=hunt_id,
            )

        async def one() -> Finding | None:
            self.session.gui.solvers_alive += 1
            self.session.notify()
            try:
                finding = await _solver_for_slice(self.deps, surface)
                if finding is not None:
                    target.findings = [f for f in target.findings if f.id != finding.id]
                    target.findings.append(finding)
                return finding
            finally:
                self.session.gui.solvers_alive = max(0, self.session.gui.solvers_alive - 1)
                self.session.notify()

        task = asyncio.create_task(one(), name=f"solver:{slice_id}")
        self._manual_solver_tasks[slice_id] = task
        task.add_done_callback(
            lambda finished, sid=slice_id: self._manual_solver_tasks.pop(sid, None)
            if self._manual_solver_tasks.get(sid) is finished
            else None
        )
        return task
