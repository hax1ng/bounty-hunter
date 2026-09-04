"""No-key role stubs. The factory can replace them with pydantic-ai Agents.

The Assistant talks to the user and never hunts.
The Organizer owns the graph; solvers are one-per-slice.
The Reporter never auto-submits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bountyhunter.models import ROLE_BLURBS, AgentRole, HuntEvent

if TYPE_CHECKING:
    from bountyhunter.organizer.service import HuntDeps


@dataclass(frozen=True)
class AgentStub:
    role: AgentRole
    blurb: str
    hunts: bool
    auto_submits: bool = False

    async def run(self, **kwargs) -> str:
        _ = kwargs
        if self.role is AgentRole.REPORTER:
            return (
                "Reporter stub: drafts an export-only platform preview. Never auto-submits."
            )
        if self.role is AgentRole.ASSISTANT:
            return "Assistant stub: I stay on the chat side. I do not hunt."
        return f"{self.role.value} local stub: ready for Organizer-owned work."


AGENT_STUBS: dict[AgentRole, AgentStub] = {
    AgentRole.ASSISTANT: AgentStub(AgentRole.ASSISTANT, ROLE_BLURBS[AgentRole.ASSISTANT], hunts=False),
    AgentRole.ORGANIZER: AgentStub(AgentRole.ORGANIZER, ROLE_BLURBS[AgentRole.ORGANIZER], hunts=False),
    AgentRole.ORACLE: AgentStub(AgentRole.ORACLE, ROLE_BLURBS[AgentRole.ORACLE], hunts=False),
    AgentRole.MAPPING: AgentStub(AgentRole.MAPPING, ROLE_BLURBS[AgentRole.MAPPING], hunts=True),
    AgentRole.SOLVER: AgentStub(AgentRole.SOLVER, ROLE_BLURBS[AgentRole.SOLVER], hunts=True),
    AgentRole.DEDUP: AgentStub(AgentRole.DEDUP, ROLE_BLURBS[AgentRole.DEDUP], hunts=True),
    AgentRole.DEVIL: AgentStub(AgentRole.DEVIL, ROLE_BLURBS[AgentRole.DEVIL], hunts=True),
    AgentRole.EVIDENCE: AgentStub(AgentRole.EVIDENCE, ROLE_BLURBS[AgentRole.EVIDENCE], hunts=True),
    AgentRole.REPORTER: AgentStub(
        AgentRole.REPORTER,
        ROLE_BLURBS[AgentRole.REPORTER],
        hunts=False,
        auto_submits=False,
    ),
}


def stub_for(role: AgentRole) -> AgentStub:
    return AGENT_STUBS[role]


def assistant_reply(
    message: str,
    *,
    target_name: str | None,
    authorized: bool | None,
    hunt_state: str | None = None,
    hunt_note: str = "",
    solvers_alive: int = 0,
    recent_events: list[HuntEvent] | None = None,
) -> str:
    """Local Assistant stand-in so the chat stays responsive without an LLM."""
    text = message.strip()
    lowered = text.lower()
    words = set(re.findall(r"[a-z]+", lowered))
    if not text:
        return "Say something and I’ll help you steer the hunt. I don’t attack anything myself."
    if any(word in lowered for word in ("submit", "auto-submit", "send to hackerone")):
        return (
            "I will never submit a report for you. When a write-up is ready, "
            "you export it and submit it on the platform yourself."
        )
    if target_name is None:
        return (
            "No Target is open. Use File → New Target… (authorization required) "
            "or File → Open Target… A Target is the project — like a Burp project file."
        )
    if authorized is False:
        return (
            f"«{target_name}» is open but not marked authorized. "
            "I won’t let the Organizer start a hunt until that is fixed."
        )
    if words & {"log", "logs", "event", "events", "status", "progress", "backend"}:
        events = recent_events or []
        lines = [event.format() for event in events[-5:]]
        recent = "\n".join(f"- {line}" for line in lines) or "- No backend events yet."
        state = hunt_state or "idle"
        note = hunt_note or "No active Organizer note."
        return (
            f"Backend status for «{target_name}»: {state}; {solvers_alive} Solver(s) active. "
            f"Organizer: {note}\nRecent events:\n{recent}"
        )
    if "scope" in lowered:
        return (
            f"Edit in-scope and out-of-scope on the Scope tab for «{target_name}». "
            "The configured Mapping role turns that into bounded SurfaceSlices — "
            "one Solver per slice, so webhook secrets stay separate from invoice export."
        )
    if any(word in lowered for word in ("hunt", "start", "scan")):
        return (
            "Hunt → Start schedules the configured graph. Roles can use local stubs or "
            "opt-in OpenAI models. The Organizer still fans one Solver task out per slice."
        )
    return (
        f"I’m the Assistant for «{target_name}». I don’t hunt. "
        "Tell me what you want the Organizer to do, or edit Scope / Agents in the tabs."
    )


async def assistant_chat(message: str, deps: HuntDeps | None) -> str:
    """Use the opt-in hosted Assistant or the no-key local fallback."""
    target = deps.target if deps is not None else None
    if deps is not None:
        config = deps.target.agent_config[AgentRole.ASSISTANT.value]
        if config.is_live:
            from bountyhunter.agents.live import assistant_response

            return await assistant_response(deps, message, deps.session.chat)
    return assistant_reply(
        message,
        target_name=target.name if target else None,
        authorized=target.authorized if target else None,
        hunt_state=target.hunt_state.value if target else None,
        hunt_note=deps.session.hunt_note if deps else "",
        solvers_alive=deps.session.gui.solvers_alive if deps else 0,
        recent_events=deps.session.events if deps else None,
    )
