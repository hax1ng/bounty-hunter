from __future__ import annotations

import json
from pathlib import Path

import pytest

from bountyhunter import MANIFEST_NAME
from bountyhunter.models import Platform, Target
from bountyhunter.safety import AuthorizationError
from bountyhunter.store import (
    HuntStore,
    create_target,
    load_target,
    save_target,
    slugify,
)


def test_slugify() -> None:
    assert slugify("Example Corp!") == "example-corp"


def test_create_requires_authorization(tmp_path: Path) -> None:
    with pytest.raises(AuthorizationError):
        create_target(
            name="Nope",
            platform=Platform.HACKERONE,
            in_scope=["*.example.com"],
            out_of_scope=[],
            authorized=False,
            target_dir=tmp_path / "nope",
        )
    assert not (tmp_path / "nope" / MANIFEST_NAME).exists()


def test_save_load_roundtrip(tmp_path: Path) -> None:
    dest = tmp_path / "acme"
    target, path = create_target(
        name="Acme",
        platform=Platform.BUGCROWD,
        in_scope=["*.acme.test", "api.acme.test"],
        out_of_scope=["blog.acme.test"],
        authorized=True,
        program_url="https://bugcrowd.com/acme",
        target_dir=dest,
        attestation="I am authorized",
    )
    assert path == dest.resolve()
    assert (dest / MANIFEST_NAME).is_file()
    assert (dest / "target.json").is_file()
    assert (dest / "events.jsonl").is_file()
    assert (dest / "scope.md").is_file()
    assert (dest / "slices").is_dir()
    assert (dest / "findings").is_dir()
    assert (dest / "evidence").is_dir()
    assert (dest / "reports").is_dir()
    assert (dest / "logs").is_dir()

    loaded, loaded_dir = load_target(dest)
    assert loaded_dir == dest.resolve()
    assert loaded.name == "Acme"
    assert loaded.platform is Platform.BUGCROWD
    assert loaded.in_scope == ["*.acme.test", "api.acme.test"]
    assert loaded.out_of_scope == ["blog.acme.test"]
    assert loaded.authorized is True
    assert "assistant" in loaded.agent_config

    store = HuntStore(dest)
    assert store.load().name == "Acme"


def test_open_manifest_file(tmp_path: Path) -> None:
    dest = tmp_path / "from-file"
    create_target(
        name="FileOpen",
        platform="hackerone",
        in_scope=["app.example.test"],
        out_of_scope=[],
        authorized=True,
        target_dir=dest,
    )
    loaded, loaded_dir = load_target(dest / MANIFEST_NAME)
    assert loaded.name == "FileOpen"
    assert loaded_dir == dest.resolve()


def test_open_visible_manifest_alias(tmp_path: Path) -> None:
    dest = tmp_path / "alias"
    create_target(
        name="Alias",
        platform=Platform.INTIGRITI,
        in_scope=["alias.test"],
        out_of_scope=[],
        authorized=True,
        target_dir=dest,
    )
    hidden = dest / MANIFEST_NAME
    visible = dest / "bountyhunt.json"
    visible.write_text(hidden.read_text(encoding="utf-8"), encoding="utf-8")
    hidden.unlink()
    loaded, loaded_dir = load_target(dest)
    assert loaded.name == "Alias"
    assert loaded_dir == dest.resolve()


def test_refuse_second_create(tmp_path: Path) -> None:
    dest = tmp_path / "once"
    create_target(
        name="Once",
        platform=Platform.CUSTOM,
        in_scope=["x.test"],
        out_of_scope=[],
        authorized=True,
        target_dir=dest,
    )
    with pytest.raises(ValueError):
        create_target(
            name="Once",
            platform=Platform.CUSTOM,
            in_scope=["x.test"],
            out_of_scope=[],
            authorized=True,
            target_dir=dest,
        )


def test_notes_roundtrip(tmp_path: Path) -> None:
    dest = tmp_path / "notes"
    target, _ = create_target(
        name="Notes",
        platform=Platform.HACKERONE,
        in_scope=["n.test"],
        out_of_scope=[],
        authorized=True,
        target_dir=dest,
        notes="hello",
    )
    target.notes = "updated notes"
    save_target(target, dest)
    loaded, _ = load_target(dest)
    assert loaded.notes == "updated notes"


def test_manifest_is_json_object(tmp_path: Path) -> None:
    dest = tmp_path / "acme"
    create_target(
        name="Json",
        platform=Platform.HACKERONE,
        in_scope=["j.test"],
        out_of_scope=[],
        authorized=True,
        target_dir=dest,
    )
    data = json.loads((dest / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert data["name"] == "Json"
    Target.model_validate(data)
