"""Verify provider profiles enforce consent, locality, scheduling, and safe failures."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from ancestryllm.cli import main
from ancestryllm.core.context import AppContext
from ancestryllm.core.errors import AncestryError, ProviderError, SecurityPolicyError
from ancestryllm.gedcom.service import GedcomService
from ancestryllm.llm.contracts import (
    DataClass,
    GenerationRequest,
    Message,
)
from ancestryllm.llm.policy import ConsentGrant
from ancestryllm.llm.service import LLMService
from ancestryllm.storage.models import LlmRunModel


def _write_person(path: Path, *, surname: str, birth: str) -> None:
    path.write_text(
        "0 HEAD\n"
        "1 GEDC\n"
        "2 VERS 5.5.5\n"
        "1 CHAR UTF-8\n"
        "0 @I1@ INDI\n"
        f"1 NAME John /{surname}/\n"
        "1 BIRT\n"
        f"2 DATE {birth}\n"
        "2 PLAC Boston, Massachusetts, USA\n"
        "1 DEAT\n"
        "2 DATE 1920\n"
        "0 TRLR\n",
        encoding="utf-8",
    )


def _identity_payload() -> dict[str, object]:
    return {
        "is_duplicate": True,
        "confidence": 0.95,
        "reasoning": "Fictional records agree.",
        "preferred_values": {
            "given_name": "",
            "surname": "",
            "birth_date": "",
            "birth_place": "",
            "death_date": "",
            "death_place": "",
            "gender": "",
        },
    }


class _FakeOllamaClient:
    def __init__(
        self,
        configuration: dict[str, Any],
        *,
        block: threading.Event | None = None,
        entered: threading.Event | None = None,
        returned: threading.Event | None = None,
        transient_failures: int = 0,
    ) -> None:
        self.configuration = configuration
        self.block = block
        self.entered = entered
        self.returned = returned
        self.transient_failures = transient_failures
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self._lock = threading.Lock()

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.calls.append(kwargs)
            if self.transient_failures:
                self.transient_failures -= 1
                raise ConnectionError("private fictional transient failure")
        if self.entered is not None:
            self.entered.set()
        if self.block is not None:
            assert self.block.wait(3)
        schema = kwargs["format"]
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        payload = {"annotations": []} if "annotations" in properties else _identity_payload()
        if self.returned is not None:
            self.returned.set()
        return {
            "message": {"content": json.dumps(payload, sort_keys=True)},
            "prompt_eval_count": 10,
            "eval_count": 5,
        }

    def close(self) -> None:
        self.closed = True


def _install_fake_ollama(
    monkeypatch: pytest.MonkeyPatch,
    *,
    block: threading.Event | None = None,
    entered: threading.Event | None = None,
    returned: threading.Event | None = None,
    transient_failures: int = 0,
) -> list[_FakeOllamaClient]:
    clients: list[_FakeOllamaClient] = []
    module = ModuleType("ollama")

    def client(**kwargs: Any) -> _FakeOllamaClient:
        instance = _FakeOllamaClient(
            kwargs,
            block=block,
            entered=entered,
            returned=returned,
            transient_failures=transient_failures,
        )
        clients.append(instance)
        return instance

    module.Client = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ollama", module)
    return clients


class _FakeOpenAIStream:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self) -> _FakeOpenAIStream:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def __iter__(self) -> Any:
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="fictional stream"))]
        )


class _FakeOpenAIClient:
    def __init__(self, configuration: dict[str, Any]) -> None:
        self.configuration = configuration
        self.calls: list[dict[str, Any]] = []
        self.streams: list[_FakeOpenAIStream] = []
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def __enter__(self) -> _FakeOpenAIClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            stream = _FakeOpenAIStream()
            self.streams.append(stream)
            return stream
        return SimpleNamespace(
            id="fictional-response",
            choices=[SimpleNamespace(message=SimpleNamespace(content="fictional answer"))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )


def _install_fake_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> list[_FakeOpenAIClient]:
    clients: list[_FakeOpenAIClient] = []
    module = ModuleType("openai")

    def client(**kwargs: Any) -> _FakeOpenAIClient:
        instance = _FakeOpenAIClient(kwargs)
        clients.append(instance)
        return instance

    module.OpenAI = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    return clients


def _create_local_profile(
    context: AppContext,
    *,
    name: str = "fictional-local",
    cache_ttl_seconds: float = 60.0,
    max_concurrency: int = 1,
    max_pending: int = 2,
    max_safe_retries: int = 0,
    base_url: str = "http://127.0.0.1:11434",
) -> None:
    context.provider_profiles.create_profile(
        name,
        "ollama",
        "fictional-model",
        {
            "base_url": base_url,
            "cache_max_entries": 8,
            "cache_ttl_seconds": cache_ttl_seconds,
            "keep_alive": "5m",
            "max_concurrency": max_concurrency,
            "max_output_tokens": 333,
            "max_pending": max_pending,
            "max_safe_retries": max_safe_retries,
            "num_batch": 16,
            "num_ctx": 4096,
            "num_gpu": 0,
            "num_thread": 2,
            "seed": 7,
            "temperature": 0,
            "timeout_seconds": 9,
        },
    )


def _gedcom_service(context: AppContext, llm: LLMService | None = None) -> GedcomService:
    return GedcomService(
        llm or context.llm,
        consent_lookup=context.provider_profiles.consent_grant,
        provider_timeout_seconds=context.config.provider_timeout_seconds,
    )


def test_operational_ollama_profile_flows_through_merge_update_quality_and_cache(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clients = _install_fake_ollama(monkeypatch)
    _create_local_profile(app_context)
    service = _gedcom_service(app_context)
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    _write_person(first, surname="Smith", birth="1850")
    _write_person(second, surname="Smyth", birth="1851")

    merged = tmp_path / "merged.ged"
    service.merge(
        [first, second],
        merged,
        provider_id="fictional-local",
        model="",
        threshold=70,
    )
    assert merged.read_text(encoding="utf-8").count(" INDI") == 1

    quality_one = tmp_path / "quality-one.md"
    quality_two = tmp_path / "quality-two.md"
    service.quality(
        first,
        quality_one,
        root_person="@I1@",
        provider_id="fictional-local",
    )
    service.quality(
        first,
        quality_two,
        root_person="@I1@",
        provider_id="fictional-local",
    )
    assert quality_one.read_text(encoding="utf-8") == quality_two.read_text(encoding="utf-8")

    releases = tmp_path / "releases"
    assert (
        service.sync(
            [
                "update",
                "--master",
                str(first),
                "--initialize-manifest",
                "--snapshot",
                f"fictional-main:other={second}",
                "--release-root",
                str(releases),
                "--no-quality-report",
                "--provider",
                "fictional-local",
            ]
        ).exit_code
        == 0
    )
    bundle = next(releases.glob("g0001-*"))
    assert (bundle / "master.ged").read_text(encoding="utf-8").count(" INDI") == 1

    assert len(clients) == 1
    assert len(clients[0].calls) == 2
    assert clients[0].configuration["host"] == "http://127.0.0.1:11434"
    for call in clients[0].calls:
        assert call["model"] == "fictional-model"
        assert call["keep_alive"] == "5m"
        assert call["options"] == {
            "temperature": 0.0,
            "num_predict": 333,
            "num_ctx": 4096,
            "num_batch": 16,
            "num_thread": 2,
            "num_gpu": 0,
            "seed": 7,
        }

    with app_context.database.session() as session:
        statuses = list(session.scalars(select(LlmRunModel.status)))
    assert statuses.count("succeeded") == 2
    assert statuses.count("cache_hit") == 2

    app_context.close()
    assert clients[0].closed


def test_remote_profile_consent_denials_precede_sdk_or_socket_use(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_context.secrets.set("openai.api_key", "configured-but-never-sent")
    app_context.provider_profiles.create_profile(
        "remote-one",
        "openai",
        "fictional-model",
    )
    app_context.provider_profiles.create_profile(
        "remote-two",
        "openai",
        "fictional-model",
    )
    app_context.provider_profiles.create_consent(
        "remote-two-consent",
        "remote-two",
        modules=["gedcom"],
        purposes=["identity_adjudication"],
        data_classes=[DataClass.DECEASED_PERSON],
        models=["fictional-model"],
    )
    app_context.provider_profiles.create_consent(
        "remote-one-deceased-consent",
        "remote-one",
        modules=["gedcom"],
        purposes=["identity_adjudication"],
        data_classes=[DataClass.DECEASED_PERSON],
        models=["fictional-model"],
    )
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    _write_person(first, surname="Smith", birth="1850")
    _write_person(second, surname="Smyth", birth="1851")
    service = _gedcom_service(app_context)
    sdk = ModuleType("openai")

    def forbidden_client(**_kwargs: Any) -> object:
        raise AssertionError("consent denial constructed an SDK client")

    sdk.OpenAI = forbidden_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", sdk)
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("consent denial opened a socket")
        ),
    )

    with pytest.raises(SecurityPolicyError) as missing:
        service.merge(
            [first, second],
            tmp_path / "missing-consent.ged",
            provider_id="remote-one",
            threshold=70,
        )
    assert missing.value.code == "CLOUD_CONSENT_REQUIRED"

    wrong_consent = app_context.provider_profiles.consent_grant("remote-two-consent")
    with pytest.raises(SecurityPolicyError) as mismatched:
        service.merge(
            [first, second],
            tmp_path / "wrong-consent.ged",
            provider_id="remote-one",
            consent=wrong_consent,
            threshold=70,
        )
    assert mismatched.value.code == "CONSENT_PROFILE_MISMATCH"
    with pytest.raises(SecurityPolicyError) as living_context:
        service.merge(
            [first, second],
            tmp_path / "insufficient-data-consent.ged",
            provider_id="remote-one",
            consent=app_context.provider_profiles.consent_grant("remote-one-deceased-consent"),
            threshold=70,
        )
    assert living_context.value.code == "CONSENT_DATA_DENIED"
    assert not (tmp_path / "missing-consent.ged").exists()
    assert not (tmp_path / "wrong-consent.ged").exists()
    assert not (tmp_path / "insufficient-data-consent.ged").exists()
    with app_context.database.session() as session:
        assert list(session.scalars(select(LlmRunModel))) == []


def test_direct_cloud_selection_uses_the_exact_consent_profile(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = _install_fake_openai(monkeypatch)
    app_context.secrets.set("openai.api_key", "fictional-key")
    app_context.provider_profiles.create_profile(
        "fictional-cloud",
        "openai",
        "fictional-model",
        {"timeout_seconds": 9},
    )
    app_context.provider_profiles.create_consent(
        "fictional-cloud-consent",
        "fictional-cloud",
        modules=["gedcom"],
        purposes=["identity_adjudication"],
        data_classes=[DataClass.DECEASED_PERSON],
        models=["fictional-model"],
    )
    consent = app_context.provider_profiles.consent_grant("fictional-cloud-consent")
    request = GenerationRequest(
        provider_id="openai",
        model="fictional-model",
        module_id="gedcom",
        purpose="identity_adjudication",
        messages=(Message(role="user", content="Fictional direct cloud request."),),
        data_classes=frozenset({DataClass.DECEASED_PERSON}),
        timeout_seconds=20,
    )

    planned = app_context.provider_profiles.resolve_request(request, consent)
    generated = app_context.llm.generate(request, consent)
    streamed = list(app_context.llm.stream(request, consent))

    assert planned.provider_id == "openai"
    assert planned.execution.profile_name == "fictional-cloud"
    assert planned.timeout_seconds == 9
    assert generated.text == "fictional answer"
    assert generated.remote is True
    assert streamed == ["fictional stream"]
    assert len(clients) == 2
    assert all(
        client.configuration["base_url"] == "https://api.openai.com/v1" for client in clients
    )
    assert all(client.closed for client in clients)
    assert clients[1].streams[0].closed


@pytest.mark.parametrize(
    ("provider_id", "model", "expected_code"),
    [
        ("anthropic", "fictional-model", "CONSENT_PROVIDER_MISMATCH"),
        ("openai", "different-model", "PROVIDER_PROFILE_MODEL_CONFLICT"),
    ],
)
def test_direct_cloud_selection_rejects_mismatch_before_provider_construction(
    app_context: AppContext,
    provider_id: str,
    model: str,
    expected_code: str,
) -> None:
    app_context.provider_profiles.create_profile(
        "fictional-cloud",
        "openai",
        "fictional-model",
    )
    app_context.provider_profiles.create_consent(
        "fictional-cloud-consent",
        "fictional-cloud",
        modules=["gedcom"],
        purposes=["identity_adjudication"],
        data_classes=[DataClass.DECEASED_PERSON],
        models=["fictional-model"],
    )
    consent = app_context.provider_profiles.consent_grant("fictional-cloud-consent")
    request = GenerationRequest(
        provider_id=provider_id,
        model=model,
        module_id="gedcom",
        purpose="identity_adjudication",
        messages=(Message(role="user", content="Fictional mismatched cloud request."),),
        data_classes=frozenset({DataClass.DECEASED_PERSON}),
    )

    with pytest.raises(AncestryError) as raised:
        app_context.llm.generate(request, consent)

    assert raised.value.code == expected_code


@pytest.mark.parametrize("operation", ["generate", "stream"])
def test_direct_cloud_selection_rejects_unlinked_consent_before_provider_construction(
    app_context: AppContext,
    operation: str,
) -> None:
    consent = ConsentGrant(
        consent_id="legacy-or-forged",
        provider_id="openai",
        allowed_modules=frozenset({"gedcom"}),
        allowed_purposes=frozenset({"identity_adjudication"}),
        allowed_data_classes=frozenset({DataClass.DECEASED_PERSON}),
        model_allowlist=("fictional-model",),
        provider_profile_name=None,
    )
    request = GenerationRequest(
        provider_id="openai",
        model="fictional-model",
        module_id="gedcom",
        purpose="identity_adjudication",
        messages=(Message(role="user", content="Fictional unlinked consent request."),),
        data_classes=frozenset({DataClass.DECEASED_PERSON}),
    )

    with pytest.raises(SecurityPolicyError) as raised:
        if operation == "generate":
            app_context.llm.generate(request, consent)
        else:
            list(app_context.llm.stream(request, consent))

    assert raised.value.code == "CONSENT_PROFILE_MISMATCH"


@pytest.mark.parametrize("operation", ["generate", "stream"])
def test_direct_cloud_selection_rejects_inconsistent_linked_consent_before_provider_construction(
    app_context: AppContext,
    operation: str,
) -> None:
    app_context.provider_profiles.create_profile(
        "fictional-anthropic",
        "anthropic",
        "fictional-model",
    )
    consent = ConsentGrant(
        consent_id="forged-provider-link",
        provider_id="openai",
        allowed_modules=frozenset({"gedcom"}),
        allowed_purposes=frozenset({"identity_adjudication"}),
        allowed_data_classes=frozenset({DataClass.DECEASED_PERSON}),
        model_allowlist=("fictional-model",),
        provider_profile_name="fictional-anthropic",
    )
    request = GenerationRequest(
        provider_id="openai",
        model="fictional-model",
        module_id="gedcom",
        purpose="identity_adjudication",
        messages=(Message(role="user", content="Fictional inconsistent consent request."),),
        data_classes=frozenset({DataClass.DECEASED_PERSON}),
    )

    with pytest.raises(SecurityPolicyError) as raised:
        if operation == "generate":
            app_context.llm.generate(request, consent)
        else:
            list(app_context.llm.stream(request, consent))

    assert raised.value.code == "CONSENT_PROVIDER_MISMATCH"


def test_non_loopback_ollama_requires_exact_profile_consent(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    clients = _install_fake_ollama(monkeypatch)
    _create_local_profile(
        app_context,
        name="fictional-remote-ollama",
        cache_ttl_seconds=0,
        base_url="https://ollama.fixture.invalid:11434",
    )
    app_context.provider_profiles.create_profile(
        "other-remote-ollama",
        "ollama",
        "fictional-model",
        {"base_url": "https://other-ollama.fixture.invalid:11434"},
    )
    for name, profile in (
        ("remote-consent", "fictional-remote-ollama"),
        ("wrong-remote-consent", "other-remote-ollama"),
    ):
        app_context.provider_profiles.create_consent(
            name,
            profile,
            modules=["gedcom"],
            purposes=["identity_adjudication"],
            data_classes=[DataClass.POSSIBLY_LIVING_PERSON],
            models=["fictional-model"],
        )
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    _write_person(first, surname="Smith", birth="1850")
    _write_person(second, surname="Smyth", birth="1851")
    service = _gedcom_service(app_context)

    with pytest.raises(SecurityPolicyError) as missing:
        service.merge(
            [first, second],
            tmp_path / "missing-remote-consent.ged",
            provider_id="fictional-remote-ollama",
            threshold=70,
        )
    assert missing.value.code == "CLOUD_CONSENT_REQUIRED"
    with pytest.raises(SecurityPolicyError) as mismatch:
        service.merge(
            [first, second],
            tmp_path / "wrong-remote-consent.ged",
            provider_id="fictional-remote-ollama",
            consent=app_context.provider_profiles.consent_grant("wrong-remote-consent"),
            threshold=70,
        )
    assert mismatch.value.code == "CONSENT_PROFILE_MISMATCH"
    assert clients == []

    output = tmp_path / "consented-remote.ged"
    service.merge(
        [first, second],
        output,
        provider_id="fictional-remote-ollama",
        consent=app_context.provider_profiles.consent_grant("remote-consent"),
        threshold=70,
    )
    assert output.is_file()
    assert len(clients) == 1
    assert clients[0].configuration["host"] == "https://ollama.fixture.invalid:11434"


def test_provider_cancellation_after_bounded_call_is_sanitized_and_never_publishes(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    returned = threading.Event()
    _install_fake_ollama(monkeypatch, returned=returned)
    _create_local_profile(app_context, cache_ttl_seconds=0)

    def cancellation_check() -> None:
        if returned.is_set():
            raise asyncio.CancelledError

    llm = LLMService(
        app_context.providers,
        app_context.database,
        profiles=app_context.provider_profiles,
        cancellation_check=cancellation_check,
    )
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    output = tmp_path / "cancelled.ged"
    _write_person(first, surname="Smith", birth="1850")
    _write_person(second, surname="Smyth", birth="1851")

    with pytest.raises(ProviderError) as raised:
        _gedcom_service(app_context, llm).merge(
            [first, second],
            output,
            provider_id="fictional-local",
            threshold=70,
        )

    assert raised.value.code == "PROVIDER_CANCELLED"
    assert not output.exists()
    with app_context.database.session() as session:
        row = session.scalars(select(LlmRunModel)).one()
    assert row.status == "aborted"
    assert row.error_code == "PROVIDER_CANCELLED"
    assert row.input_payload is None
    assert row.output_payload is None


@pytest.mark.parametrize(
    ("mode", "expected_code"),
    (("timeout", "PROVIDER_TIMEOUT"), ("malformed", "PROVIDER_OUTPUT_INVALID")),
)
def test_profile_timeout_and_malformed_results_are_sanitized_and_never_cached(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
    expected_code: str,
) -> None:
    sensitive = "private-fictional-provider-payload"
    calls = 0
    module = ModuleType("ollama")

    class BadClient:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        def chat(self, **_kwargs: Any) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if mode == "timeout":
                raise TimeoutError(sensitive)
            return {"message": {"content": sensitive}}

        def close(self) -> None:
            return None

    module.Client = BadClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ollama", module)
    _create_local_profile(app_context)
    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    _write_person(first, surname="Smith", birth="1850")
    _write_person(second, surname="Smyth", birth="1851")
    service = _gedcom_service(app_context)

    for attempt in range(2):
        output = tmp_path / f"{mode}-{attempt}.ged"
        with pytest.raises(ProviderError) as raised:
            service.merge(
                [first, second],
                output,
                provider_id="fictional-local",
                threshold=70,
            )
        assert raised.value.code == expected_code
        assert sensitive not in raised.value.render()
        assert sensitive not in repr(raised.value.details)
        assert not output.exists()
    assert calls == 2


def test_provider_none_is_socket_free_with_every_credential_sdk_and_profile_present(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for secret_name in (
        "openai.api_key",
        "anthropic.api_key",
        "gemini.api_key",
        "openrouter.api_key",
    ):
        app_context.secrets.set(secret_name, f"configured-{secret_name}")
    _create_local_profile(app_context)
    constructed: list[str] = []
    for module_name, constructor_name in (
        ("ollama", "Client"),
        ("openai", "OpenAI"),
        ("anthropic", "Anthropic"),
    ):
        module = ModuleType(module_name)

        def forbidden(*_args: Any, _name: str = module_name, **_kwargs: Any) -> object:
            constructed.append(_name)
            raise AssertionError(f"provider none constructed {_name}")

        setattr(module, constructor_name, forbidden)
        monkeypatch.setitem(sys.modules, module_name, module)
    google = ModuleType("google")
    genai = ModuleType("google.genai")

    def forbidden_gemini(*_args: Any, **_kwargs: Any) -> object:
        constructed.append("google.genai")
        raise AssertionError("provider none constructed google.genai")

    genai.Client = forbidden_gemini  # type: ignore[attr-defined]
    google.genai = genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider none opened a socket")
        ),
    )

    first = tmp_path / "first.ged"
    second = tmp_path / "second.ged"
    _write_person(first, surname="Smith", birth="1850")
    _write_person(second, surname="Different", birth="1875")
    service = _gedcom_service(app_context)
    service.merge([first, second], tmp_path / "offline.ged", provider_id="none")
    service.quality(
        first,
        tmp_path / "offline-quality.md",
        root_person="@I1@",
        provider_id="none",
        model="configured-but-ignored",
    )
    releases = tmp_path / "releases"
    assert (
        service.sync(
            [
                "update",
                "--master",
                str(first),
                "--initialize-manifest",
                "--snapshot",
                f"fictional-main:other={second}",
                "--release-root",
                str(releases),
                "--no-quality-report",
                "--provider",
                "none",
                "--model",
                "configured-but-ignored",
            ]
        ).exit_code
        == 0
    )
    assert constructed == []
    with app_context.database.session() as session:
        assert list(session.scalars(select(LlmRunModel))) == []


def test_profile_scheduler_rejects_overflow_and_releases_waiters(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    entered = threading.Event()
    clients = _install_fake_ollama(monkeypatch, block=release, entered=entered)
    _create_local_profile(
        app_context,
        cache_ttl_seconds=0,
        max_concurrency=1,
        max_pending=2,
    )
    request = GenerationRequest(
        provider_id="fictional-local",
        model="",
        module_id="gedcom",
        purpose="identity_adjudication",
        messages=(Message(role="user", content="Fictional bounded identity pair."),),
        response_schema={
            "type": "object",
            "properties": {
                "is_duplicate": {"type": "boolean"},
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
                "preferred_values": {"type": "object"},
            },
            "required": ["is_duplicate", "confidence", "reasoning", "preferred_values"],
        },
        data_classes=frozenset({DataClass.DECEASED_PERSON}),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(app_context.llm.generate, request)
        assert entered.wait(2)
        second = executor.submit(app_context.llm.generate, request)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if any(lane.waiting == 1 for lane in app_context.llm.execution._lanes.values()):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("second provider request did not enter the bounded queue")

        with pytest.raises(ProviderError) as raised:
            app_context.llm.generate(request)
        assert raised.value.code == "PROVIDER_QUEUE_FULL"
        release.set()
        assert first.result(timeout=2).parsed == _identity_payload()
        assert second.result(timeout=2).parsed == _identity_payload()

    assert len(clients) == 1
    assert len(clients[0].calls) == 2


def test_single_flight_requests_still_obey_total_pending_bound(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    entered = threading.Event()
    clients = _install_fake_ollama(monkeypatch, block=release, entered=entered)
    _create_local_profile(
        app_context,
        cache_ttl_seconds=60,
        max_concurrency=1,
        max_pending=2,
    )
    request = GenerationRequest(
        provider_id="fictional-local",
        model="",
        module_id="gedcom",
        purpose="identity_adjudication",
        messages=(Message(role="user", content="Fictional duplicate single-flight request."),),
        response_schema={
            "type": "object",
            "properties": {
                "is_duplicate": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
                "preferred_values": {"type": "object"},
            },
            "required": ["is_duplicate", "confidence", "reasoning", "preferred_values"],
        },
        data_classes=frozenset({DataClass.DECEASED_PERSON}),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(app_context.llm.generate, request)
        assert entered.wait(2)
        second = executor.submit(app_context.llm.generate, request)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if any(lane.admitted == 2 for lane in app_context.llm.execution._lanes.values()):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("duplicate request did not enter single-flight admission")

        with pytest.raises(ProviderError) as raised:
            app_context.llm.generate(request)
        assert raised.value.code == "PROVIDER_QUEUE_FULL"
        release.set()
        assert first.result(timeout=2).parsed == _identity_payload()
        assert second.result(timeout=2).parsed == _identity_payload()

    assert len(clients) == 1
    assert len(clients[0].calls) == 1


def test_profile_retry_setting_is_an_explicit_effective_opt_in(
    app_context: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients = _install_fake_ollama(monkeypatch, transient_failures=1)
    _create_local_profile(
        app_context,
        cache_ttl_seconds=0,
        max_safe_retries=1,
    )
    monkeypatch.setattr(app_context.llm, "_wait_for_retry", lambda _delay: None)
    request = GenerationRequest(
        provider_id="fictional-local",
        model="",
        module_id="gedcom",
        purpose="identity_adjudication",
        messages=(Message(role="user", content="Fictional retry profile request."),),
        response_schema={
            "type": "object",
            "properties": {
                "is_duplicate": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
                "preferred_values": {"type": "object"},
            },
            "required": ["is_duplicate", "confidence", "reasoning", "preferred_values"],
        },
        data_classes=frozenset({DataClass.DECEASED_PERSON}),
    )

    planned = app_context.provider_profiles.resolve_request(request)
    result = app_context.llm.generate(request)

    assert planned.max_safe_retries == 1
    assert result.parsed == _identity_payload()
    assert len(clients) == 1
    assert len(clients[0].calls) == 2


def test_profile_settings_fail_closed_before_provider_construction(
    app_context: AppContext,
) -> None:
    with pytest.raises(AncestryError) as unknown:
        app_context.provider_profiles.create_profile(
            "invalid-setting",
            "ollama",
            "fictional-model",
            {"unbounded_magic": True},
        )
    assert unknown.value.code == "PROVIDER_PROFILE_SETTING_UNKNOWN"

    with pytest.raises(AncestryError) as unsafe_endpoint:
        app_context.provider_profiles.create_profile(
            "unsafe-endpoint",
            "ollama",
            "fictional-model",
            {"base_url": "http://192.0.2.10:11434"},
        )
    assert unsafe_endpoint.value.code == "ENDPOINT_REJECTED"

    _create_local_profile(app_context, name="model-locked")
    with pytest.raises(AncestryError) as model_conflict:
        app_context.provider_profiles.resolve_request(
            GenerationRequest(
                provider_id="model-locked",
                model="different-model",
                module_id="gedcom",
                purpose="identity_adjudication",
                messages=(Message(role="user", content="Fictional profile plan."),),
            )
        )
    assert model_conflict.value.code == "PROVIDER_PROFILE_MODEL_CONFLICT"


def test_cli_creates_typed_operational_profile(
    app_context: AppContext,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "providers",
                "create",
                "cli-local",
                "--provider",
                "ollama",
                "--model",
                "fictional-model",
                "--setting",
                "base_url=http://127.0.0.1:11434",
                "--setting",
                "max_concurrency=2",
                "--setting",
                "cache_ttl_seconds=30",
            ],
            app_context,
        )
        == 0
    )
    capsys.readouterr()
    planned = app_context.provider_profiles.resolve_request(
        GenerationRequest(
            provider_id="cli-local",
            model="",
            module_id="gedcom",
            purpose="identity_adjudication",
            messages=(Message(role="user", content="Fictional profile plan."),),
        )
    )
    assert planned.provider_id == "ollama"
    assert planned.model == "fictional-model"
    assert planned.execution.profile_name == "cli-local"
    assert planned.execution.base_url == "http://127.0.0.1:11434"
    assert planned.execution.max_concurrency == 2
    assert planned.execution.cache_ttl_seconds == 30
