"""Subscription-backed Anthropic calls through the local ``claude`` CLI.

The Claude Code CLI owns its Claude Pro/Max OAuth login and refresh tokens.
Bounty Hunter shells out to ``claude --print`` for a single, output-only turn and
never reads, copies, or persists those tokens.  Every turn runs with **all tools
disabled** (``--tools ""``), no MCP servers (``--strict-mcp-config`` with none
provided), no slash commands, no session persistence, and inside an empty
temporary directory — the same "transform only, no recon, no network" guarantee
as the Codex subscription route.

``--json-schema`` makes the model return validated structured output; the
result is read from the ``structured_output`` field of ``--output-format json``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from bountyhunter.agents.reasoning import claude_effort
from bountyhunter.models import ReasoningLevel

OutputT = TypeVar("OutputT", bound=BaseModel)


class ClaudeSubscriptionError(RuntimeError):
    """The Claude subscription route could not produce a valid response."""


@dataclass(frozen=True)
class ClaudeSubscriptionResult:
    output: BaseModel
    total_tokens: int = 0


def _structured_schema(output_type: type[OutputT]) -> dict[str, Any]:
    """JSON Schema handed to ``claude --json-schema`` for validated output."""
    return output_type.model_json_schema()


def _extract_total_tokens(payload: dict[str, Any]) -> int:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return 0
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    total = 0
    for key in keys:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def _coerce_output(payload: dict[str, Any], output_type: type[OutputT]) -> OutputT:
    """Validate the CLI's structured output (or a JSON ``result`` string)."""
    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        try:
            return output_type.model_validate(structured)
        except ValueError as exc:
            raise ClaudeSubscriptionError(
                "Claude returned structured output that did not match the schema"
            ) from exc
    # Fallback: some CLI versions place the JSON in ``result`` as a string.
    text = payload.get("result")
    if isinstance(text, str) and text.strip():
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines.pop()
            cleaned = "\n".join(lines).strip()
        try:
            return output_type.model_validate_json(cleaned)
        except ValueError as exc:
            raise ClaudeSubscriptionError("Claude returned invalid structured output") from exc
    raise ClaudeSubscriptionError("Claude completed without a structured response")


async def run_claude_subscription(
    *,
    model: str,
    reasoning: ReasoningLevel,
    instructions: str,
    prompt: str,
    output_type: type[OutputT],
    timeout_seconds: float = 120,
) -> ClaudeSubscriptionResult:
    """Run one ephemeral, output-only Claude turn using the CLI subscription."""
    executable = shutil.which("claude")
    if executable is None:
        raise ClaudeSubscriptionError("Claude CLI is not installed")

    schema = json.dumps(_structured_schema(output_type))
    system = (
        instructions
        + "\n\nYou are an output-only role. Do not call tools, inspect files, "
        "access the network, or delegate. Return only the requested JSON object."
    )
    # NOTE: no ``--bare`` — that flag forces ANTHROPIC_API_KEY auth and would
    # bypass the subscription this route exists to use.
    command = [
        executable,
        "--print",
        "--model",
        model,
        "--output-format",
        "json",
        "--tools",
        "",  # disable every built-in tool
        "--strict-mcp-config",  # ignore any ambient MCP servers
        "--disable-slash-commands",
        "--no-session-persistence",
        "--permission-mode",
        "default",
        "--append-system-prompt",
        system,
        "--json-schema",
        schema,
    ]
    effort = claude_effort(reasoning)
    if effort is not None:
        command += ["--effort", effort]
    command.append(prompt)

    with tempfile.TemporaryDirectory(prefix="bountyhunter-claude-") as workdir:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=Path(workdir),
            )
        except OSError as exc:
            raise ClaudeSubscriptionError(f"Could not launch Claude CLI: {exc}") from exc
        try:
            async with asyncio.timeout(timeout_seconds):
                stdout_bytes, stderr_bytes = await process.communicate()
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ClaudeSubscriptionError("Claude subscription request timed out") from exc
        except asyncio.CancelledError:
            # The Organizer kill switch cancels the awaiting task. Do not leave a
            # detached provider subprocess running after the graph has stopped.
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            raise

        if process.returncode != 0:
            detail = (stderr_bytes or b"").decode(errors="replace").strip()
            raise ClaudeSubscriptionError(
                f"Claude CLI exited with status {process.returncode}: {detail or 'no detail'}"
            )
        try:
            payload = json.loads((stdout_bytes or b"").decode(errors="replace") or "{}")
        except json.JSONDecodeError as exc:
            raise ClaudeSubscriptionError("Claude CLI returned non-JSON output") from exc
        if not isinstance(payload, dict):
            raise ClaudeSubscriptionError("Claude CLI returned an unexpected payload")
        if payload.get("is_error") or payload.get("subtype") not in (None, "success"):
            raise ClaudeSubscriptionError(
                str(payload.get("result") or payload.get("subtype") or "Claude turn failed")
            )
        output = _coerce_output(payload, output_type)
        return ClaudeSubscriptionResult(
            output=output, total_tokens=_extract_total_tokens(payload)
        )
