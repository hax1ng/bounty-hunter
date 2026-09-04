"""Ephemeral provider credentials and connection state.

Secrets never live on ``Target`` and are never written by ``HuntStore``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from pydantic import BaseModel, Field, SecretStr


class OpenAIConnection(BaseModel):
    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    source: str = "none"
    use_subscription_first: bool = True
    codex_available: bool = False
    codex_authenticated: bool | None = None
    codex_auth_method: str = "not checked"

    model_config = {"extra": "forbid"}

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.get_secret_value().strip())

    @property
    def status(self) -> str:
        if self.use_subscription_first and self.codex_authenticated:
            fallback = f" · API fallback ({self.source})" if self.configured else ""
            return f"ChatGPT subscription first{fallback}"
        if self.use_subscription_first and self.codex_authenticated is None:
            fallback = " · API fallback ready" if self.configured else ""
            return f"ChatGPT subscription first · not checked{fallback}"
        if self.configured:
            return f"OpenAI API key configured ({self.source})"
        if self.use_subscription_first:
            return "ChatGPT subscription unavailable · no API fallback"
        return "OpenAI key not configured"

    @property
    def ready(self) -> bool:
        return bool(
            (self.use_subscription_first and self.codex_authenticated)
            or self.configured
        )

    @property
    def subscription_authenticated(self) -> bool | None:
        """Uniform accessor shared with :class:`AnthropicConnection`."""
        return self.codex_authenticated

    def mark_subscription_unchecked(self) -> None:
        self.codex_authenticated = None
        self.codex_auth_method = "not checked"

    def reveal(self) -> str | None:
        return self.api_key.get_secret_value() if self.api_key else None

    def use_session_key(self, value: str) -> None:
        cleaned = value.strip()
        self.api_key = SecretStr(cleaned) if cleaned else None
        self.source = "session" if cleaned else "none"

    def reload_environment(self) -> None:
        value = os.getenv("OPENAI_API_KEY", "").strip()
        self.api_key = SecretStr(value) if value else None
        self.source = "environment" if value else "none"

    def refresh_subscription(self) -> bool:
        """Check Codex CLI authentication without reading or copying its token."""
        executable = shutil.which("codex")
        self.codex_available = executable is not None
        if executable is None:
            self.codex_authenticated = False
            self.codex_auth_method = "Codex CLI not installed"
            return False
        try:
            result = subprocess.run(
                [executable, "login", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            self.codex_authenticated = False
            self.codex_auth_method = "Codex login check failed"
            return False
        output = f"{result.stdout}\n{result.stderr}".strip()
        self.codex_authenticated = result.returncode == 0 and "logged in" in output.lower()
        if self.codex_authenticated:
            self.codex_auth_method = (
                "ChatGPT subscription"
                if "chatgpt" in output.lower()
                else "Codex authenticated"
            )
        else:
            self.codex_auth_method = "Not signed in; run `codex login`"
        return bool(self.codex_authenticated)


def load_openai_connection() -> OpenAIConnection:
    connection = OpenAIConnection()
    connection.reload_environment()
    return connection


class AnthropicConnection(BaseModel):
    """Claude access: the logged-in Claude subscription first, API key second.

    The subscription route uses the existing ``claude`` (Claude Code) login and
    its included Claude Pro/Max usage.  Bounty Hunter only reads
    ``claude auth status`` output; it never reads, copies, or persists the CLI's
    OAuth tokens.
    """

    api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)
    source: str = "none"
    use_subscription_first: bool = True
    claude_available: bool = False
    claude_authenticated: bool | None = None
    claude_auth_method: str = "not checked"
    subscription_type: str = ""

    model_config = {"extra": "forbid"}

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_key.get_secret_value().strip())

    @property
    def subscription_label(self) -> str:
        return self.subscription_type.title() if self.subscription_type else "Claude"

    @property
    def status(self) -> str:
        if self.use_subscription_first and self.claude_authenticated:
            plan = f" ({self.subscription_type})" if self.subscription_type else ""
            fallback = f" · API fallback ({self.source})" if self.configured else ""
            return f"Claude subscription first{plan}{fallback}"
        if self.use_subscription_first and self.claude_authenticated is None:
            fallback = " · API fallback ready" if self.configured else ""
            return f"Claude subscription first · not checked{fallback}"
        if self.configured:
            return f"Anthropic API key configured ({self.source})"
        if self.use_subscription_first:
            return "Claude subscription unavailable · no API fallback"
        return "Anthropic key not configured"

    @property
    def ready(self) -> bool:
        return bool(
            (self.use_subscription_first and self.claude_authenticated)
            or self.configured
        )

    @property
    def subscription_authenticated(self) -> bool | None:
        return self.claude_authenticated

    def mark_subscription_unchecked(self) -> None:
        self.claude_authenticated = None
        self.claude_auth_method = "not checked"

    def reveal(self) -> str | None:
        return self.api_key.get_secret_value() if self.api_key else None

    def use_session_key(self, value: str) -> None:
        cleaned = value.strip()
        self.api_key = SecretStr(cleaned) if cleaned else None
        self.source = "session" if cleaned else "none"

    def reload_environment(self) -> None:
        value = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.api_key = SecretStr(value) if value else None
        self.source = "environment" if value else "none"

    def refresh_subscription(self) -> bool:
        """Check ``claude`` login without reading or copying its OAuth token."""
        executable = shutil.which("claude")
        self.claude_available = executable is not None
        if executable is None:
            self.claude_authenticated = False
            self.claude_auth_method = "Claude CLI not installed"
            self.subscription_type = ""
            return False
        try:
            result = subprocess.run(
                [executable, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            self.claude_authenticated = False
            self.claude_auth_method = "Claude login check failed"
            self.subscription_type = ""
            return False
        info = _parse_claude_status(result.stdout)
        logged_in = bool(info.get("loggedIn"))
        auth_method = str(info.get("authMethod", "") or "")
        # An "apiKey" auth method is not the included subscription entitlement;
        # only OAuth/claude.ai logins count as the subscription-first route.
        is_subscription = logged_in and auth_method.lower() not in {"", "apikey"}
        self.claude_authenticated = is_subscription
        self.subscription_type = str(info.get("subscriptionType", "") or "")
        if is_subscription:
            plan = f" ({self.subscription_type})" if self.subscription_type else ""
            self.claude_auth_method = f"Claude subscription via {auth_method}{plan}"
        elif logged_in:
            self.claude_auth_method = "Signed in with an API key, not a subscription"
        else:
            self.claude_auth_method = "Not signed in; run `claude auth login`"
        return is_subscription


def _parse_claude_status(stdout: str) -> dict[str, object]:
    """Parse ``claude auth status`` JSON, tolerating extra non-JSON lines."""
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced JSON object in the output.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def load_anthropic_connection() -> AnthropicConnection:
    connection = AnthropicConnection()
    connection.reload_environment()
    return connection
