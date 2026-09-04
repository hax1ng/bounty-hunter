"""App-level settings: recent targets, last window prefs."""

from __future__ import annotations

import json
from pathlib import Path

from platformdirs import user_config_dir
from pydantic import BaseModel, Field

from bountyhunter import APP_ID

MAX_RECENT = 12


def settings_path() -> Path:
    return Path(user_config_dir(APP_ID, appauthor=False)) / "settings.json"


class RecentTarget(BaseModel):
    name: str
    path: str
    platform: str = ""


class AppSettings(BaseModel):
    recent: list[RecentTarget] = Field(default_factory=list)
    last_target_path: str | None = None
    host: str = "127.0.0.1"
    port: int = 8088
    # Prefer the user's existing subscription entitlement before metered API
    # billing.  These are application preferences, never Target hunt data:
    # Codex/ChatGPT for OpenAI roles, Claude Code login for Anthropic roles.
    openai_subscription_first: bool = True
    anthropic_subscription_first: bool = True

    def remember(self, name: str, path: Path, platform: str = "") -> None:
        resolved = str(path.expanduser().resolve())
        self.recent = [item for item in self.recent if item.path != resolved]
        self.recent.insert(0, RecentTarget(name=name, path=resolved, platform=platform))
        self.recent = self.recent[:MAX_RECENT]
        self.last_target_path = resolved

    def forget(self, path: Path | str) -> None:
        resolved = str(Path(path).expanduser().resolve())
        self.recent = [item for item in self.recent if item.path != resolved]
        if self.last_target_path == resolved:
            self.last_target_path = self.recent[0].path if self.recent else None

    def clear_recent(self) -> None:
        self.recent = []
        self.last_target_path = None


def load_settings(path: Path | None = None) -> AppSettings:
    dest = path or settings_path()
    if not dest.exists():
        return AppSettings()
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
        return AppSettings.model_validate(data)
    except (OSError, json.JSONDecodeError, ValueError):
        return AppSettings()


def save_settings(settings: AppSettings, path: Path | None = None) -> Path:
    dest = path or settings_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(settings.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return dest
