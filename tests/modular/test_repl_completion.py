from __future__ import annotations

import socket
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent, Completer, ThreadedCompleter
from prompt_toolkit.document import Document

from ancestryllm.console.completion import CompletionSnapshot, create_completer
from ancestryllm.console.router import SessionRouter
from ancestryllm.core.context import AppContext
from ancestryllm.core.secrets import ENVIRONMENT_NAMES


def _values(completer: Completer, text: str) -> list[str]:
    document = Document(text, cursor_position=len(text))
    event = CompleteEvent(completion_requested=True)
    return [item.text for item in completer.get_completions(document, event)]


def _completion(
    app_context: AppContext,
    tmp_path: Path,
    *,
    profiles: tuple[str, ...] = ("fictional-local", "fictional-remote"),
    consents: tuple[str, ...] = ("deceased-only",),
) -> tuple[SessionRouter, Completer]:
    router = SessionRouter(app_context)
    snapshot = CompletionSnapshot(profiles=profiles, consents=consents)
    return router, create_completer(router, snapshot, tmp_path)


def test_snapshot_is_frozen_normalized_and_factory_is_threaded(
    app_context: AppContext, tmp_path: Path
) -> None:
    snapshot = CompletionSnapshot(
        profiles=("z-profile", "a-profile", "a-profile", ""),
        consents=("safe", "safe"),
    )
    assert snapshot.profiles == ("a-profile", "z-profile")
    assert snapshot.consents == ("safe",)
    with pytest.raises(FrozenInstanceError):
        snapshot.profiles = ()  # type: ignore[misc]

    completer = create_completer(SessionRouter(app_context), snapshot, tmp_path)
    assert isinstance(completer, ThreadedCompleter)


def test_root_and_active_module_contexts_are_distinct_and_disabled_modules_hidden(
    app_context: AppContext, tmp_path: Path
) -> None:
    app_context.config.enabled_modules = {"gedcom", "providers", "secrets"}
    router, completer = _completion(app_context, tmp_path)

    root = _values(completer, "")
    assert root == sorted(root)
    assert {"database", "gedcom", "help", "modules", "providers", "secrets", "use"} <= set(root)
    assert "rootsmagic" not in root
    assert _values(completer, "use ") == ["gedcom", "providers", "secrets"]

    router.active_module = "gedcom"
    active = _values(completer, "")
    assert {"back", "info", "run", "set", "show", "unset"} <= set(active)
    assert "use" not in active
    assert "gedcom" not in active
    assert _values(completer, "run s") == ["subtree", "sync"]


def test_direct_and_run_invocations_complete_actions_unused_flags_and_enums(
    app_context: AppContext, tmp_path: Path
) -> None:
    app_context.config.enabled_modules = {"gedcom"}
    router, completer = _completion(app_context, tmp_path)

    assert _values(completer, "gedcom q") == ["quality"]
    flags = _values(completer, "gedcom merge -")
    assert "--gedcom-version" in flags
    assert "--provider" in flags
    assert "-o" in flags
    assert _values(completer, "gedcom merge --gedcom-version ") == ["5.5.1", "5.5.5"]
    assert _values(completer, "gedcom subtree --scope d") == ["descendants"]

    unused = _values(completer, "gedcom merge --provider fictional-local --")
    assert "--provider" not in unused
    assert "--consent" in unused

    router.active_module = "gedcom"
    assert "--scope" in _values(completer, "run subtree --")
    router.module_options["action"] = "subtree"
    assert _values(completer, "run --scope d") == ["descendants"]


def test_snapshot_only_sources_profiles_consents_and_static_secret_references(
    app_context: AppContext, tmp_path: Path
) -> None:
    app_context.config.enabled_modules = {"gedcom", "providers", "secrets"}
    _router, completer = _completion(app_context, tmp_path)

    assert _values(completer, "gedcom merge --provider ") == [
        "fictional-local",
        "fictional-remote",
    ]
    assert _values(completer, "gedcom merge --consent ") == ["deceased-only"]
    assert _values(completer, "providers consent sample --profile fictional-r") == [
        "fictional-remote"
    ]
    assert _values(completer, "secrets set ") == sorted(ENVIRONMENT_NAMES)
    assert set(ENVIRONMENT_NAMES.values()).isdisjoint(_values(completer, "secrets set "))


def test_dynamic_and_sensitive_values_are_never_suggested(
    app_context: AppContext, tmp_path: Path
) -> None:
    app_context.config.enabled_modules = {"people", "prompts", "rootsmagic"}
    _router, completer = _completion(app_context, tmp_path)

    assert _values(completer, "rootsmagic query --tree ") == []
    assert _values(completer, "rootsmagic query --model ") == []
    assert _values(completer, "rootsmagic export --root-person-id ") == []
    assert _values(completer, "prompts show ") == ["--version"]
    assert _values(completer, "people add ") == [
        "--living-status",
        "--notes",
        "--workspace",
    ]
    assert _values(completer, "people add Fictional --living-status ") == []


def test_set_and_unset_complete_names_without_exposing_values(
    app_context: AppContext, tmp_path: Path
) -> None:
    app_context.config.enabled_modules = {"gedcom"}
    router, completer = _completion(app_context, tmp_path)
    router.active_module = "gedcom"

    names = _values(completer, "set ")
    assert {"action", "gedcom_version", "output", "provider", "root_person"} <= set(names)
    assert _values(completer, "set action q") == ["quality"]
    router.module_options.update({"action": "subtree", "scope": "ancestors"})
    assert _values(completer, "set scope d") == ["descendants"]
    assert _values(completer, "set root_person ") == []
    assert _values(completer, "unset ") == ["action", "scope"]


def test_file_completion_is_confined_private_and_bounded(
    app_context: AppContext, tmp_path: Path
) -> None:
    (tmp_path / "visible.ged").write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    (tmp_path / ".hidden.ged").write_text("private", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "fictional.ged").write_text("0 HEAD\n0 TRLR\n", encoding="utf-8")
    hidden_directory = tmp_path / ".hidden-dir"
    hidden_directory.mkdir()
    (hidden_directory / "private.ged").write_text("private", encoding="utf-8")
    outside = tmp_path.parent / "outside-private.ged"
    outside.write_text("private", encoding="utf-8")

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "workspace.db").write_text("private", encoding="utf-8")
    tree_dir = tmp_path / "trees"
    tree_dir.mkdir()
    (tree_dir / "private.rmtree").write_text("private", encoding="utf-8")
    app_context.config.data_dir = data_dir
    app_context.config.family_tree_dirs = [tree_dir]

    symlink = tmp_path / "linked.ged"
    try:
        symlink.symlink_to(outside)
    except OSError:
        pass

    for index in range(100):
        (tmp_path / f"fixture-{index:03d}.ged").write_text("0 TRLR\n", encoding="utf-8")

    app_context.config.enabled_modules = {"gedcom"}
    _router, completer = _completion(app_context, tmp_path)
    root_values = _values(completer, "gedcom merge ")
    assert len(root_values) <= 64
    assert _values(completer, "gedcom merge vis") == ["visible.ged"]
    assert _values(completer, "gedcom merge nes") == ["nested/"]
    assert ".hidden.ged" not in root_values
    assert _values(completer, "gedcom merge data") == []
    assert _values(completer, "gedcom merge trees") == []
    assert _values(completer, "gedcom merge linked") == []

    assert _values(completer, "gedcom merge nested/fi") == ["nested/fictional.ged"]
    assert _values(completer, 'gedcom merge "vis') == ["visible.ged"]
    assert _values(completer, "gedcom merge ../") == []
    assert _values(completer, f"gedcom merge {outside}") == []
    assert _values(completer, "gedcom merge .hidden-dir/") == []


def test_completion_does_not_touch_services_keyring_or_network(
    app_context: AppContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExplodingService:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError(f"completion touched service attribute {name}")

    app_context.database = ExplodingService()  # type: ignore[assignment]
    app_context.secrets = ExplodingService()  # type: ignore[assignment]
    app_context.providers = ExplodingService()  # type: ignore[assignment]
    app_context.provider_profiles = ExplodingService()  # type: ignore[assignment]
    app_context.llm = ExplodingService()  # type: ignore[assignment]
    app_context.prompts = ExplodingService()  # type: ignore[assignment]
    app_context.research = ExplodingService()  # type: ignore[assignment]

    def reject_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("completion attempted network access")

    monkeypatch.setattr(socket, "socket", reject_socket)
    app_context.config.enabled_modules = {"gedcom", "secrets"}
    _router, completer = _completion(app_context, tmp_path)

    assert _values(completer, "gedcom merge --provider f") == [
        "fictional-local",
        "fictional-remote",
    ]
    assert _values(completer, "secrets set open") == [
        "openai.api_key",
        "openrouter.api_key",
        "openrouter.management_key",
    ]
    assert _values(completer, 'gedcom merge "unfinished') == []
