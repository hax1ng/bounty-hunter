"""Typed domain models for targets, hunt state, events, and agent policy.

The GUI deliberately keeps domain state in these models instead of in NiceGUI
widgets.  This makes the same target usable from a future headless organizer and
keeps persistence/versioning in one place.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


class Platform(StrEnum):
    HACKERONE = "hackerone"
    BUGCROWD = "bugcrowd"
    INTIGRITI = "intigriti"
    IMMUNEFI = "immunefi"
    YESWEHACK = "yeswehack"
    SYNACK = "synack"
    CUSTOM = "custom"


class ReasoningLevel(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ModelProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"


class AgentRole(StrEnum):
    ASSISTANT = "assistant"
    ORGANIZER = "organizer"
    ORACLE = "oracle"
    MAPPING = "mapping"
    SOLVER = "solver"
    DEDUP = "dedup"
    DEVIL = "devil"
    EVIDENCE = "evidence"
    REPORTER = "reporter"


class HuntState(StrEnum):
    IDLE = "idle"
    MAPPING = "mapping"
    HUNTING = "hunting"
    PAUSED = "paused"
    REVIEWING = "reviewing"
    COMPLETE = "complete"
    STOPPED = "stopped"


class SliceKind(StrEnum):
    WEB = "web"
    API = "api"
    MOBILE = "mobile"
    SOURCE = "source"
    WALLET = "wallet"
    INFRA = "infra"
    OTHER = "other"


class FindingStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_EVIDENCE = "needs_evidence"
    REPORTABLE = "reportable"
    KILLED = "killed"
    DUPLICATE = "duplicate"
    INFORMATIONAL = "informational"


class Severity(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WeaknessClass(StrEnum):
    # web2
    IDOR = "idor"
    BROKEN_AUTHZ = "broken_authz"
    AUTH_BYPASS = "auth_bypass"
    SSRF = "ssrf"
    XSS = "xss"
    SQLI = "sqli"
    INJECTION = "injection"                        # command / template / header / other
    SECRET_EXPOSURE = "secret_exposure"
    BUSINESS_LOGIC = "business_logic"
    RACE = "race"
    CSRF = "csrf"
    DESERIALIZATION = "deserialization"
    MISCONFIG = "misconfig"
    # web3
    WEB3_ACCOUNTING = "web3_accounting"            # share/accounting desync, ERC4626
    WEB3_ACCESS_CONTROL = "web3_access_control"
    WEB3_ORACLE = "web3_oracle"                    # price-oracle manipulation
    WEB3_REENTRANCY = "web3_reentrancy"
    WEB3_FLASH_LOAN = "web3_flash_loan"
    WEB3_SIGNATURE_REPLAY = "web3_signature_replay"
    WEB3_PROXY_UPGRADE = "web3_proxy_upgrade"
    # llm / ai
    LLM_PROMPT_INJECTION = "llm_prompt_injection"
    LLM_INDIRECT_INJECTION = "llm_indirect_injection"
    LLM_SYSTEM_PROMPT_LEAK = "llm_system_prompt_leak"
    LLM_TOOL_ABUSE = "llm_tool_abuse"              # RCE / SSRF via agent tools
    LLM_DATA_EXFIL = "llm_data_exfil"
    OTHER = "other"


class DirectiveStatus(StrEnum):
    PROPOSED = "proposed"     # Oracle emitted it
    QUEUED = "queued"         # Organizer scheduled Mapping/Solver for it
    TESTED = "tested"         # Solver ran
    CONFIRMED = "confirmed"   # produced a Finding
    REFUTED = "refuted"       # expected signal absent
    DEFERRED = "deferred"     # requires operator/live-Solver follow-up
    ABANDONED = "abandoned"   # Organizer deprioritized it (dead area / out of budget)


ROLE_BLURBS: dict[AgentRole, str] = {
    AgentRole.ASSISTANT: "Answers questions and summarizes backend state and logs. Never hunts.",
    AgentRole.ORGANIZER: "Continuously owns the hunt graph, task queue, and async fan-out.",
    AgentRole.ORACLE: "Strategist: drives hypothesis, chain, and impact-escalation loops.",
    AgentRole.MAPPING: "Maps the declared attack surface into bounded SurfaceSlices.",
    AgentRole.SOLVER: "One agent per slice (webhook secrets ≠ invoice export).",
    AgentRole.DEDUP: "Duplicate-risk check against known reports.",
    AgentRole.DEVIL: "Kills informative, spam, and weak reports.",
    AgentRole.EVIDENCE: "Reproduction steps, screenshot/video paths.",
    AgentRole.REPORTER: "Platform write-up. Never auto-submits.",
}

DEFAULT_ROLE_MODELS: dict[AgentRole, tuple[str, ReasoningLevel]] = {
    AgentRole.ASSISTANT: ("gpt-5.6-luna", ReasoningLevel.LOW),
    AgentRole.ORGANIZER: ("gpt-5.6-terra", ReasoningLevel.MEDIUM),
    AgentRole.ORACLE: ("gpt-5.6-sol", ReasoningLevel.XHIGH),
    AgentRole.MAPPING: ("gpt-5.6-terra", ReasoningLevel.MEDIUM),
    AgentRole.SOLVER: ("gpt-5.6-sol", ReasoningLevel.HIGH),
    AgentRole.DEDUP: ("gpt-5.6-luna", ReasoningLevel.LOW),
    AgentRole.DEVIL: ("gpt-5.6-terra", ReasoningLevel.HIGH),
    AgentRole.EVIDENCE: ("gpt-5.6-terra", ReasoningLevel.MEDIUM),
    AgentRole.REPORTER: ("gpt-5.6-terra", ReasoningLevel.MEDIUM),
}

# Model presets offered per provider.  Users may still type any other model id;
# these are only the quick picks shown in the GUI.  Anthropic ids double as CLI
# aliases for the subscription route (`opus`, `sonnet`, `haiku` also work).
OPENAI_MODEL_PRESETS: tuple[str, ...] = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
)
ANTHROPIC_MODEL_PRESETS: tuple[str, ...] = (
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
)
MODEL_PRESETS: dict[ModelProvider, tuple[str, ...]] = {
    ModelProvider.OPENAI: OPENAI_MODEL_PRESETS,
    ModelProvider.ANTHROPIC: ANTHROPIC_MODEL_PRESETS,
    ModelProvider.LOCAL: (),
}
DEFAULT_PROVIDER_MODEL: dict[ModelProvider, str] = {
    ModelProvider.OPENAI: "gpt-5.6-terra",
    ModelProvider.ANTHROPIC: "claude-sonnet-4-6",
    ModelProvider.LOCAL: "",
}
PROVIDER_LABELS: dict[ModelProvider, str] = {
    ModelProvider.OPENAI: "OpenAI",
    ModelProvider.ANTHROPIC: "Anthropic",
    ModelProvider.LOCAL: "Local stub",
}


def model_presets_for(provider: ModelProvider) -> list[str]:
    return list(MODEL_PRESETS.get(provider, ()))


def default_model_for(provider: ModelProvider) -> str:
    return DEFAULT_PROVIDER_MODEL.get(provider, "")


class AgentRoleConfig(BaseModel):
    role: AgentRole
    provider: ModelProvider = ModelProvider.OPENAI
    model: str = "gpt-5.6-terra"
    reasoning: ReasoningLevel = ReasoningLevel.MEDIUM
    enabled: bool = True
    use_llm: bool = False

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _local_provider_is_stub(self) -> AgentRoleConfig:
        # A local-stub provider never makes a hosted call, so ``use_llm`` is
        # meaningless there; keep the two consistent at load time.
        if self.provider is ModelProvider.LOCAL:
            self.use_llm = False
        return self

    @property
    def is_live(self) -> bool:
        """True only when this role should make a real hosted-model call."""
        return self.enabled and self.use_llm and self.provider is not ModelProvider.LOCAL

    def as_policy(self) -> ModelPolicy:
        return ModelPolicy(
            model=self.model,
            provider=self.provider,
            reasoning=self.reasoning,
            enabled=self.enabled,
            use_llm=self.use_llm,
        )


class ModelPolicy(BaseModel):
    """Provider-neutral settings used by :mod:`agents.factory`.

    ``use_llm`` is intentionally false by default: opening the application must
    never require a provider key or trigger a remote model call.
    """

    provider: ModelProvider = ModelProvider.OPENAI
    model: str = "gpt-5.6-terra"
    reasoning: ReasoningLevel = ReasoningLevel.MEDIUM
    enabled: bool = True
    use_llm: bool = False

    model_config = {"extra": "forbid"}


def default_agent_config() -> dict[str, AgentRoleConfig]:
    out: dict[str, AgentRoleConfig] = {}
    for role, (model, reasoning) in DEFAULT_ROLE_MODELS.items():
        out[role.value] = AgentRoleConfig(role=role, model=model, reasoning=reasoning)
    return out


_ARTIFACT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_artifact_id(value: str) -> str:
    """Validate ids that are later used as artifact filenames."""
    if not _ARTIFACT_ID.fullmatch(value):
        raise ValueError(
            "Artifact id must be 1-128 characters containing only letters, numbers, '.', '_', or '-'"
        )
    return value


class SurfaceSlice(BaseModel):
    """One bounded attack surface. Solvers never share slices."""

    id: str
    title: str
    asset: str
    kind: SliceKind = SliceKind.WEB
    notes: str = ""
    status: str = "pending"
    out_of_scope_overlap: list[str] = Field(default_factory=list)
    solver_task_id: str | None = None

    model_config = {"extra": "forbid"}

    _validate_id = field_validator("id")(_safe_artifact_id)


class Finding(BaseModel):
    id: str
    title: str
    severity: Severity = Severity.NONE
    status: FindingStatus = FindingStatus.DRAFT
    slice_id: str | None = None
    hypothesis_id: str | None = None
    summary: str = ""
    killed_reason: str = ""
    duplicate_risk: str = "unknown"
    advocate_verdict: str = "pending"
    reproduction_steps: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    _validate_id = field_validator("id")(_safe_artifact_id)


class Hypothesis(BaseModel):
    """A specific, falsifiable weakness to test. Upstream of a Finding."""

    id: str
    dedupe_key: str                     # derived by the Organizer, not the model
    slice_id: str | None = None         # probe an existing slice…
    proposed_asset: str = ""            # …or a fresh area (routes through Mapping first)
    weakness: WeaknessClass = WeaknessClass.OTHER
    weakness_detail: str = ""           # fill when weakness is OTHER
    statement: str                      # the falsifiable claim
    test: str                           # concrete in-scope probe for the Solver
    expected_signal: str                # what confirms vs refutes it
    priority: Priority = Priority.MEDIUM
    rationale: str = ""
    status: DirectiveStatus = DirectiveStatus.PROPOSED
    finding_id: str | None = None       # set when CONFIRMED

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _exactly_one_surface_target(self) -> Hypothesis:
        if bool((self.slice_id or "").strip()) == bool(self.proposed_asset.strip()):
            raise ValueError("Hypothesis must name exactly one slice_id or proposed_asset")
        return self


class Chain(BaseModel):
    """Two or more findings combined into higher impact."""

    id: str
    dedupe_key: str
    finding_ids: list[str] = Field(min_length=2, max_length=6)
    combined_severity: Severity = Severity.NONE
    impact: str                         # real-world impact of the combination
    demonstration: str                  # concrete in-scope step to prove it
    priority: Priority = Priority.HIGH
    rationale: str = ""
    status: DirectiveStatus = DirectiveStatus.PROPOSED

    model_config = {"extra": "forbid"}

    @field_validator("finding_ids")
    @classmethod
    def _distinct_findings(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("A chain must contain distinct finding ids")
        return value


class Escalation(BaseModel):
    """Push one finding's demonstrated impact / severity higher."""

    id: str
    dedupe_key: str
    finding_id: str
    from_severity: Severity = Severity.NONE
    to_severity: Severity = Severity.NONE
    probe: str                          # next in-scope step to demonstrate more impact
    impact: str
    priority: Priority = Priority.HIGH
    rationale: str = ""
    status: DirectiveStatus = DirectiveStatus.PROPOSED

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _severity_must_increase(self) -> Escalation:
        if _severity_rank(self.to_severity) <= _severity_rank(self.from_severity):
            raise ValueError("Escalation to_severity must be higher than from_severity")
        return self


class HuntTask(BaseModel):
    id: str
    role: AgentRole
    slice_id: str | None = None
    status: str = "queued"
    detail: str = ""

    model_config = {"extra": "forbid"}


class ReportDraft(BaseModel):
    """Export-only report text produced by the Reporter adapter."""

    id: str
    finding_id: str
    title: str
    platform: Platform
    markdown: str
    created_at: datetime = Field(default_factory=utcnow)

    model_config = {"extra": "forbid"}


class EventKind(StrEnum):
    STATUS = "status"
    TASK = "task"
    RESULT = "result"
    SAFETY = "safety"
    FILE = "file"
    CHAT = "chat"
    ERROR = "error"


class EventLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    AGENT = "agent"


class HuntEvent(BaseModel):
    """One append-only event shown by the bottom logger."""

    id: UUID = Field(default_factory=uuid4)
    ts: datetime = Field(default_factory=utcnow)
    hunt_id: str = ""
    phase: HuntState = HuntState.IDLE
    kind: EventKind = EventKind.STATUS
    level: EventLevel = EventLevel.INFO
    source: str = "workspace"
    message: str
    slice_id: str | None = None

    model_config = {"extra": "forbid"}

    def format(self) -> str:
        stamp = self.ts.astimezone().strftime("%H:%M:%S")
        return (
            f"[{stamp}] {self.phase.value:<9} {self.kind.value:<7} "
            f"{self.source}: {self.message}"
        )


class GuiState(BaseModel):
    """Ephemeral GUI-only state kept beside, never inside, ``Target``."""

    active_tab: str = "dashboard"
    open_tabs: list[str] = Field(default_factory=list)
    selected_slice_id: str | None = None
    selected_finding_id: str | None = None
    selected_report_id: str | None = None
    hunt_id: str = ""
    hunt_cycle: int = 0
    solvers_alive: int = 0
    event_drawer_open: bool = True
    assistant_busy: bool = False

    model_config = {"extra": "forbid"}


class Target(BaseModel):
    """A Target is the project unit: New / Open / Save."""

    schema_version: int = 1
    id: UUID = Field(default_factory=uuid4)
    name: str
    platform: Platform = Platform.HACKERONE
    program_url: str = ""
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    authorized: bool = False
    authorization_attestation: str = ""
    authorized_at: datetime | None = None
    notes: str = ""
    # Continuous mode is opt-in at the model layer for backwards compatibility
    # with existing target files and headless callers.  The GUI enables it for
    # newly-created targets so an interactive hunt keeps working until Stop or
    # the optional time limit is reached.
    continuous_hunt: bool = False
    hunt_time_limit_minutes: int = Field(default=0, ge=0, le=10_080)
    hunt_cycle_delay_seconds: int = Field(default=30, ge=1, le=3_600)
    agent_config: dict[str, AgentRoleConfig] = Field(default_factory=default_agent_config)
    slices: list[SurfaceSlice] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    reports: list[ReportDraft] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    chains: list[Chain] = Field(default_factory=list)
    escalations: list[Escalation] = Field(default_factory=list)
    hunt_state: HuntState = HuntState.IDLE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Target name is required")
        return cleaned

    @field_validator("in_scope", "out_of_scope")
    @classmethod
    def _strip_scope(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in value:
            line = item.strip()
            if not line or line.startswith("#") or line in seen:
                continue
            seen.add(line)
            out.append(line)
        return out

    @model_validator(mode="after")
    def _ensure_roles(self) -> Target:
        defaults = default_agent_config()
        unknown = sorted(set(self.agent_config) - set(defaults))
        if unknown:
            raise ValueError(f"Unknown agent role configuration: {', '.join(unknown)}")
        for key, config in self.agent_config.items():
            if config.role.value != key:
                raise ValueError(
                    f"Agent configuration key {key!r} contains role {config.role.value!r}"
                )
        for key, cfg in defaults.items():
            if key not in self.agent_config:
                self.agent_config[key] = cfg
        return self

    def fingerprint(self) -> str:
        """Canonical snapshot used for dirty detection (excludes updated_at)."""
        return self.model_dump_json(exclude={"updated_at"}, exclude_none=False)

    def touch(self) -> None:
        self.updated_at = utcnow()

    def set_scope_from_text(self, in_scope_text: str, out_of_scope_text: str) -> None:
        self.in_scope = _lines(in_scope_text)
        self.out_of_scope = _lines(out_of_scope_text)

    def in_scope_text(self) -> str:
        return "\n".join(self.in_scope)

    def out_of_scope_text(self) -> str:
        return "\n".join(self.out_of_scope)

    def to_manifest_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _lines(text: str) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines


def parse_scope_text(text: str) -> list[str]:
    return _lines(text)


class AuthorizationError(ValueError):
    """Raised when a hunt action is attempted without authorization."""


def _severity_rank(severity: Severity) -> int:
    return list(Severity).index(severity)
