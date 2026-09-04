"""Single persistence boundary for a Target and all hunt artifacts.

``HuntStore`` owns the on-disk layout. The module-level functions are retained
for callers from the first prototype; they delegate to ``HuntStore`` rather
than implementing another persistence system.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bountyhunter import ALT_MANIFEST_NAME, MANIFEST_NAME
from bountyhunter.models import HuntEvent, Target, utcnow
from bountyhunter.safety import require_authorized_to_create

TARGET_FILE_NAME = "target.json"
EVENTS_FILE_NAME = "events.jsonl"
TARGET_SUBDIRS = ("slices", "findings", "reports", "evidence", "logs")


class TargetStoreError(ValueError):
    pass


HuntStoreError = TargetStoreError


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip()).strip("-").lower()
    return slug or "target"


def default_target_dir(name: str) -> Path:
    return Path.home() / "bountyhunter" / "targets" / f"{slugify(name)}.bountyhunt"


def is_manifest_name(path: Path) -> bool:
    return path.name in {MANIFEST_NAME, ALT_MANIFEST_NAME, TARGET_FILE_NAME}


def resolve_target_dir(path: Path) -> Path:
    """Accept a project folder, ``target.json``, or ``.bountyhunt.json``."""
    path = path.expanduser().resolve()
    if path.is_file() and is_manifest_name(path):
        return path.parent
    if path.is_dir():
        return path
    if path.suffix == ".json" and "bountyhunt" in path.name:
        return path.parent
    raise TargetStoreError(f"Not a target folder or {MANIFEST_NAME}: {path}")


class HuntStore:
    """Read/write one target folder and its append-only event log.

    ``target.json`` is canonical. ``.bountyhunt.json`` is a compatibility
    manifest and direct-open handle for targets from the first prototype.
    """

    def __init__(self, root: Path, *, preferred_manifest: Path | None = None) -> None:
        self.root = root.expanduser().resolve()
        self.preferred_manifest = preferred_manifest.resolve() if preferred_manifest else None

    @classmethod
    def from_path(cls, path: Path) -> HuntStore:
        raw = path.expanduser()
        if not raw.exists():
            raise TargetStoreError(f"Path does not exist: {raw}")
        preferred = raw.resolve() if raw.is_file() and is_manifest_name(raw) else None
        return cls(resolve_target_dir(raw), preferred_manifest=preferred)

    @property
    def target_path(self) -> Path:
        return self.root / TARGET_FILE_NAME

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    @property
    def events_path(self) -> Path:
        return self.root / EVENTS_FILE_NAME

    def ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in TARGET_SUBDIRS:
            (self.root / name).mkdir(exist_ok=True)
        self.events_path.touch(exist_ok=True)

    def _existing_manifest(self) -> Path:
        candidates = [self.target_path, self.manifest_path, self.root / ALT_MANIFEST_NAME]
        if self.preferred_manifest is not None:
            candidates.insert(0, self.preferred_manifest)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise TargetStoreError(
            f"No {TARGET_FILE_NAME}, {MANIFEST_NAME}, or {ALT_MANIFEST_NAME} in {self.root}"
        )

    def exists(self) -> bool:
        try:
            self._existing_manifest()
        except TargetStoreError:
            return False
        return True

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)

    def save(self, target: Target, *, create: bool = False) -> Path:
        if create:
            require_authorized_to_create(target.authorized)
        self.ensure_layout()
        target.touch()
        payload = target.model_dump(mode="json")
        self._atomic_json(self.target_path, payload)
        self._atomic_json(self.manifest_path, payload)
        (self.root / "scope.md").write_text(render_scope_md(target), encoding="utf-8")
        (self.root / "notes.md").write_text(target.notes or "", encoding="utf-8")
        self._write_artifacts(target)
        return self.root

    def save_target(self, target: Target, *, create: bool = False) -> Path:
        """Named alias used by organizer/application code in older sketches."""
        return self.save(target, create=create)

    def _write_artifacts(self, target: Target) -> None:
        for surface in target.slices:
            self._atomic_json(
                self.root / "slices" / f"{surface.id}.json",
                surface.model_dump(mode="json"),
            )
        for finding in target.findings:
            self._atomic_json(
                self.root / "findings" / f"{finding.id}.json",
                finding.model_dump(mode="json"),
            )
        for report in target.reports:
            safe_id = slugify(report.id)
            (self.root / "reports" / f"{safe_id}.md").write_text(
                report.markdown,
                encoding="utf-8",
            )

    def load(self) -> Target:
        manifest = self._existing_manifest()
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TargetStoreError(f"Invalid JSON in {manifest}: {exc}") from exc
        if not isinstance(data, dict):
            raise TargetStoreError("Target manifest is not a JSON object")
        version = data.get("schema_version", 1)
        if version != 1:
            raise TargetStoreError(f"Unsupported schema_version {version}")
        notes_file = self.root / "notes.md"
        if notes_file.exists():
            data["notes"] = notes_file.read_text(encoding="utf-8")
        return Target.model_validate(data)

    def load_target(self) -> Target:
        return self.load()

    def append_event(self, event: HuntEvent) -> Path:
        self.ensure_layout()
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")
        return self.events_path

    def read_events(self, *, limit: int = 500) -> list[HuntEvent]:
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text(encoding="utf-8").splitlines()[-limit:]
        events: list[HuntEvent] = []
        for line in lines:
            try:
                events.append(HuntEvent.model_validate_json(line))
            except ValueError:
                # One corrupt logger line must not make a Target unopenable.
                continue
        return events


def render_scope_md(target: Target) -> str:
    in_lines = "\n".join(f"- `{item}`" for item in target.in_scope) or "- *(empty)*"
    out_lines = "\n".join(f"- `{item}`" for item in target.out_of_scope) or "- *(empty)*"
    return (
        f"# Scope — {target.name}\n\n"
        f"Platform: **{target.platform.value}**\n\n"
        f"Program: {target.program_url or '*(none)*'}\n\n"
        f"Authorized: **{'yes' if target.authorized else 'NO'}**\n\n"
        f"## In scope\n\n{in_lines}\n\n"
        f"## Out of scope\n\n{out_lines}\n"
    )


# Compatibility helpers; all storage still goes through HuntStore.
def ensure_layout(target_dir: Path) -> None:
    HuntStore(target_dir).ensure_layout()


def manifest_path(target_dir: Path) -> Path:
    store = HuntStore(target_dir)
    try:
        return store._existing_manifest()
    except TargetStoreError:
        return store.manifest_path


def find_manifest(target_dir: Path) -> Path:
    return HuntStore(target_dir)._existing_manifest()


def save_target(target: Target, target_dir: Path, *, create: bool = False) -> Path:
    return HuntStore(target_dir).save(target, create=create)


def load_target(path: Path) -> tuple[Target, Path]:
    store = HuntStore.from_path(path)
    return store.load(), store.root


def create_target(
    *,
    name: str,
    platform,
    in_scope: list[str],
    out_of_scope: list[str],
    authorized: bool,
    program_url: str = "",
    notes: str = "",
    target_dir: Path | None = None,
    attestation: str = "",
) -> tuple[Target, Path]:
    require_authorized_to_create(authorized)
    from bountyhunter.models import Platform as PlatformEnum

    if not isinstance(platform, PlatformEnum):
        platform = PlatformEnum(platform)
    target = Target(
        name=name,
        platform=platform,
        program_url=program_url.strip(),
        in_scope=in_scope,
        out_of_scope=out_of_scope,
        authorized=True,
        authorization_attestation=attestation,
        authorized_at=utcnow(),
        notes=notes,
    )
    store = HuntStore(target_dir or default_target_dir(name))
    if store.exists():
        raise TargetStoreError(f"A target already exists at {store.root}")
    store.save(target, create=True)
    return target, store.root
