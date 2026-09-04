"""Subscription-backed OpenAI calls through the local Codex app server.

The Codex CLI owns its ChatGPT login and refresh tokens.  Bounty Hunter only
talks JSON-RPC over stdio and never reads, copies, or persists those tokens.
No dynamic tools are registered; built-in execution/network features are
disabled and every ephemeral thread runs in an empty, read-only sandbox.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from pydantic_ai.profiles.openai import OpenAIJsonSchemaTransformer

from bountyhunter import __version__

OutputT = TypeVar("OutputT", bound=BaseModel)


class CodexSubscriptionError(RuntimeError):
    """The subscription route could not produce a valid model response."""


@dataclass(frozen=True)
class CodexSubscriptionResult:
    output: BaseModel
    total_tokens: int = 0


class _CodexAppServer:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self._request_id = 0
        self._agent_text = ""
        self.total_tokens = 0
        self._turn_finished = False
        self._turn_error: str | None = None

    def begin_turn(self) -> None:
        """Reset notification state before starting or retrying a turn."""
        self._agent_text = ""
        self._turn_finished = False
        self._turn_error = None

    async def send(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise CodexSubscriptionError("Codex app server stdin is unavailable")
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        await self.send(payload)

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        await self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = await self.read_message()
            if message.get("id") == request_id:
                if "error" in message:
                    raise CodexSubscriptionError(_error_text(message["error"]))
                return message.get("result", {})
            await self.handle_notification(message)

    async def read_message(self) -> dict[str, Any]:
        if self.process.stdout is None:
            raise CodexSubscriptionError("Codex app server stdout is unavailable")
        line = await self.process.stdout.readline()
        if not line:
            code = self.process.returncode
            raise CodexSubscriptionError(
                f"Codex app server exited before completing the request (exit {code})"
            )
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    async def handle_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method", "")
        params = message.get("params", {})
        if not isinstance(params, dict):
            params = {}

        # Refuse any server-initiated request. These roles are transform-only.
        if message.get("id") is not None and method:
            await self.send(
                {
                    "id": message["id"],
                    "error": {"code": -32001, "message": "Tools are disabled for this role"},
                }
            )
            return

        if method == "item/completed":
            item = params.get("item", params)
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = item.get("text", "")
                if text and item.get("phase") != "commentary":
                    self._agent_text = str(text)
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage", {})
            if isinstance(usage, dict):
                total = usage.get("total", {})
                if isinstance(total, dict):
                    self.total_tokens = int(total.get("totalTokens", 0) or 0)
        elif method == "turn/completed":
            turn = params.get("turn", {})
            if not isinstance(turn, dict):
                self._turn_error = "Codex returned an invalid turn result"
            elif str(turn.get("status", "")) == "failed":
                self._turn_error = _error_text(turn.get("error", "unknown error"))
            self._turn_finished = True

    async def wait_for_turn(self) -> str:
        while not self._turn_finished:
            message = await self.read_message()
            await self.handle_notification(message)
        if self._turn_error:
            raise CodexSubscriptionError(self._turn_error)
        if not self._agent_text.strip():
            raise CodexSubscriptionError("Codex completed without a final response")
        return self._agent_text


def _error_text(value: Any) -> str:
    if isinstance(value, dict):
        message = str(value.get("message", "Codex request failed"))
        info = value.get("codexErrorInfo")
        return f"{message} ({info})" if info else message
    return str(value)


def _structured_schema(output_type: type[OutputT]) -> dict[str, Any]:
    schema = output_type.model_json_schema()
    return OpenAIJsonSchemaTransformer(schema, strict=True).walk()


def _parse_output(text: str, output_type: type[OutputT]) -> OutputT:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        cleaned = "\n".join(lines).strip()
    try:
        return output_type.model_validate_json(cleaned)
    except ValueError as error:
        parse_error = error
        # Output schemas normally force a bare JSON object. Be tolerant of a
        # short accidental preamble/fence rather than failing an otherwise
        # valid subscription turn.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if 0 <= start < end and (start != 0 or end != len(cleaned) - 1):
            try:
                return output_type.model_validate_json(cleaned[start : end + 1])
            except ValueError as extracted_error:
                parse_error = extracted_error
        detail = _validation_summary(parse_error)
        raise CodexSubscriptionError(
            f"Codex returned invalid {output_type.__name__} structured output: {detail}"
        ) from parse_error


def _validation_summary(error: ValueError) -> str:
    """Return useful schema diagnostics without persisting model-output values."""
    if isinstance(error, ValidationError):
        messages: list[str] = []
        for item in error.errors(include_input=False, include_url=False)[:4]:
            path = ".".join(str(part) for part in item.get("loc", ())) or "response"
            messages.append(f"{path}: {item.get('msg', 'invalid value')}")
        if messages:
            return "; ".join(messages)
    return "response was not valid JSON for the requested schema"


async def run_codex_subscription(
    *,
    model: str,
    reasoning: str,
    instructions: str,
    prompt: str,
    output_type: type[OutputT],
    timeout_seconds: float = 120,
) -> CodexSubscriptionResult:
    """Run one ephemeral, output-only Codex turn using ChatGPT authentication."""
    executable = shutil.which("codex")
    if executable is None:
        raise CodexSubscriptionError("Codex CLI is not installed")

    # Feature flags are defense in depth on top of no dynamic tools, read-only
    # sandboxing, disabled network access, and explicit transform-only prompts.
    command = [
        executable,
        "app-server",
        "-c",
        "mcp_servers={}",
        "--disable",
        "shell_tool",
        "--disable",
        "unified_exec",
        "--disable",
        "apps",
        "--disable",
        "plugins",
        "--disable",
        "recommended_plugins",
        "--disable",
        "skill_search",
        "--disable",
        "skill_mcp_dependency_install",
        "--disable",
        "tool_suggest",
        "--disable",
        "browser_use",
        "--disable",
        "browser_use_external",
        "--disable",
        "in_app_browser",
        "--disable",
        "computer_use",
        "--disable",
        "image_generation",
        "--disable",
        "view_image",
        "--disable",
        "multi_agent",
        "--disable",
        "sleep_tool",
    ]

    with tempfile.TemporaryDirectory(prefix="bountyhunter-codex-") as workdir:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=Path(workdir),
        )
        client = _CodexAppServer(process)
        try:
            async with asyncio.timeout(timeout_seconds):
                await client.request(
                    "initialize",
                    {
                        "clientInfo": {"name": "bountyhunter", "version": __version__},
                        "capabilities": {"experimentalApi": True},
                    },
                )
                await client.notify("initialized", {})
                started = await client.request(
                    "thread/start",
                    {
                        "model": model,
                        "personality": "pragmatic",
                        "baseInstructions": (
                            instructions
                            + "\n\nYou are an output-only role. Do not call tools, inspect files, "
                            "access the network, or delegate. Return only the requested JSON object."
                        ),
                        "cwd": workdir,
                        "approvalPolicy": "never",
                        "sandbox": "read-only",
                        "ephemeral": True,
                    },
                )
                thread = started.get("thread", {}) if isinstance(started, dict) else {}
                thread_id = thread.get("id") if isinstance(thread, dict) else None
                if not thread_id:
                    raise CodexSubscriptionError("Codex did not return a thread id")
                await client.request(
                    "turn/start",
                    _turn_params(
                        thread_id=thread_id,
                        prompt=prompt,
                        reasoning=reasoning,
                        output_type=output_type,
                    ),
                )
                text = await client.wait_for_turn()
                try:
                    output = _parse_output(text, output_type)
                except CodexSubscriptionError as first_error:
                    # Structured-output failures can be transient. Retry once
                    # in the same ephemeral thread with the identical schema;
                    # no tools or extra target data are introduced.
                    client.begin_turn()
                    await client.request(
                        "turn/start",
                        _turn_params(
                            thread_id=thread_id,
                            prompt=(
                                "Your previous response did not validate against the required "
                                f"{output_type.__name__} JSON schema ({first_error}). "
                                "Correct it now. Return only the complete JSON object, with every "
                                "required field and no commentary."
                            ),
                            reasoning=reasoning,
                            output_type=output_type,
                        ),
                    )
                    retry_text = await client.wait_for_turn()
                    try:
                        output = _parse_output(retry_text, output_type)
                    except CodexSubscriptionError as retry_error:
                        raise CodexSubscriptionError(
                            f"Codex structured-output retry failed: {retry_error}"
                        ) from retry_error
                return CodexSubscriptionResult(output=output, total_tokens=client.total_tokens)
        except TimeoutError as exc:
            raise CodexSubscriptionError("Codex subscription request timed out") from exc
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.wait()


def _turn_params(
    *,
    thread_id: str,
    prompt: str,
    reasoning: str,
    output_type: type[OutputT],
) -> dict[str, Any]:
    return {
        "threadId": thread_id,
        "input": [{"type": "text", "text": prompt}],
        "effort": reasoning,
        "outputSchema": _structured_schema(output_type),
        "approvalPolicy": "never",
        "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
    }
