"""Pure helpers that turn an Oracle plan into persisted, deduplicated directives.

Kept free of Session/asyncio so the keying and absorption logic is unit-testable
on its own. The Organizer owns scheduling; this module only converts and keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bountyhunter.agents.live import (
    ChainProposal,
    EscalationProposal,
    HypothesisProposal,
    OraclePlan,
)
from bountyhunter.models import Chain, Escalation, Hypothesis, Target


def _slug(text: str, limit: int = 60) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")[:limit]


def hypothesis_key(proposal: HypothesisProposal) -> str:
    where = (proposal.slice_id or proposal.proposed_asset or "?").strip() or "?"
    return f"h:{where}:{proposal.weakness.value}:{_slug(proposal.statement)}"


def chain_key(proposal: ChainProposal) -> str:
    return "c:" + "+".join(sorted(proposal.finding_ids))


def escalation_key(proposal: EscalationProposal) -> str:
    return f"e:{proposal.finding_id}:{proposal.to_severity.value}"


@dataclass
class AbsorbResult:
    """The freshly persisted directives from one plan (already deduped)."""

    hypotheses: list[Hypothesis] = field(default_factory=list)
    chains: list[Chain] = field(default_factory=list)
    escalations: list[Escalation] = field(default_factory=list)


def stub_oracle_plan() -> OraclePlan:
    """Local stand-in: no strategy engine without a model."""
    return OraclePlan(
        iteration_summary="local stub: no strategy engine without a model",
        converged=True,
        stop_reason="stub",
    )


def absorb_plan(target: Target, plan: OraclePlan, seen: set[str]) -> AbsorbResult:
    """Convert proposals to persisted records, skipping any key already in ``seen``.

    Mutates ``target`` (appends directives) and ``seen`` (adds new keys). Chains
    and escalations that reference unknown findings are dropped. Returns only the
    fresh records so the caller can schedule work for them.
    """
    fresh = AbsorbResult()
    known_findings = {finding.id: finding for finding in target.findings}

    for proposal in plan.hypotheses:
        key = hypothesis_key(proposal)
        if key in seen:
            continue
        seen.add(key)
        hypothesis = Hypothesis(
            id=f"hyp-{len(target.hypotheses) + 1:03d}",
            dedupe_key=key,
            slice_id=proposal.slice_id,
            proposed_asset=proposal.proposed_asset,
            weakness=proposal.weakness,
            weakness_detail=proposal.weakness_detail,
            statement=proposal.statement,
            test=proposal.test,
            expected_signal=proposal.expected_signal,
            priority=proposal.priority,
            rationale=proposal.rationale,
        )
        target.hypotheses.append(hypothesis)
        fresh.hypotheses.append(hypothesis)

    for proposal in plan.chains:
        key = chain_key(proposal)
        if key in seen or not all(fid in known_findings for fid in proposal.finding_ids):
            continue
        seen.add(key)
        chain = Chain(
            id=f"chain-{len(target.chains) + 1:03d}",
            dedupe_key=key,
            finding_ids=list(proposal.finding_ids),
            combined_severity=proposal.combined_severity,
            impact=proposal.impact,
            demonstration=proposal.demonstration,
            priority=proposal.priority,
            rationale=proposal.rationale,
        )
        target.chains.append(chain)
        fresh.chains.append(chain)

    for proposal in plan.escalations:
        key = escalation_key(proposal)
        finding = known_findings.get(proposal.finding_id)
        if key in seen or finding is None:
            continue
        levels = list(type(finding.severity))
        if levels.index(proposal.to_severity) <= levels.index(finding.severity):
            continue
        seen.add(key)
        escalation = Escalation(
            id=f"esc-{len(target.escalations) + 1:03d}",
            dedupe_key=key,
            finding_id=proposal.finding_id,
            # The Organizer trusts persisted finding state, not an Oracle's
            # possibly stale claim about the starting severity.
            from_severity=finding.severity,
            to_severity=proposal.to_severity,
            probe=proposal.probe,
            impact=proposal.impact,
            priority=proposal.priority,
            rationale=proposal.rationale,
        )
        target.escalations.append(escalation)
        fresh.escalations.append(escalation)

    return fresh
