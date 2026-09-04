"""Provider-neutral mapping from :class:`ReasoningLevel` to per-provider knobs.

The same reasoning level drives three routes so the operator's choice stays
consistent no matter which provider or credential path a role uses:

* OpenAI Responses API  -> ``openai_reasoning_effort`` (raw level string)
* Anthropic API          -> ``anthropic_effort`` / extended thinking
* Anthropic subscription -> ``claude --effort <level>``

A companion output-token budget scales the request/usage caps with the level so
higher reasoning is not truncated before it can answer.
"""

from __future__ import annotations

from bountyhunter.models import ReasoningLevel

# Anthropic effort accepts low/medium/high/xhigh/max.  ``none`` means "do not
# think": the API route disables extended thinking and the CLI route omits
# ``--effort`` entirely.
_CLAUDE_EFFORT: dict[ReasoningLevel, str | None] = {
    ReasoningLevel.NONE: None,
    ReasoningLevel.LOW: "low",
    ReasoningLevel.MEDIUM: "medium",
    ReasoningLevel.HIGH: "high",
    ReasoningLevel.XHIGH: "xhigh",
    ReasoningLevel.MAX: "max",
}

# Output-token budget per level.  Extended thinking counts against output, so
# these keep the request's ``max_tokens`` and pydantic-ai ``UsageLimits`` above
# the thinking budget for the chosen level.
_OUTPUT_BUDGET: dict[ReasoningLevel, int] = {
    ReasoningLevel.NONE: 2000,
    ReasoningLevel.LOW: 4000,
    ReasoningLevel.MEDIUM: 8000,
    ReasoningLevel.HIGH: 12000,
    ReasoningLevel.XHIGH: 16000,
    ReasoningLevel.MAX: 24000,
}


def claude_effort(level: ReasoningLevel) -> str | None:
    """Return the Anthropic effort string, or ``None`` for no extended thinking."""
    return _CLAUDE_EFFORT.get(level)


def output_token_budget(level: ReasoningLevel, *, verbose: bool = False) -> int:
    """Output-token ceiling for a reasoning level.

    ``verbose`` roles (the Reporter) get extra room for their longer narrative.
    """
    base = _OUTPUT_BUDGET.get(level, 8000)
    return base + 4000 if verbose else base
