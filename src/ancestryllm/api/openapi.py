"""Deterministic checked-in OpenAPI generation for desktop client tooling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from fastapi import FastAPI

from ancestryllm.api.app import create_app
from ancestryllm.api.settings import ApiSettings
from ancestryllm.application.executor import CommandExecutor
from ancestryllm.core.commands import ModuleDescriptor

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
OPENAPI_ARTIFACT = _REPOSITORY_ROOT / "docs" / "api" / "openapi-v1.json"


class _EmptyRegistry:
    def descriptors(self) -> Sequence[ModuleDescriptor]:
        return ()


def contract_app() -> FastAPI:
    return create_app(
        settings=ApiSettings(
            bearer_token="O" * 43,
            app_build="openapi-contract",
            sidecar_build="openapi-contract",
            provider_id="none",
        ),
        registry=_EmptyRegistry(),
        executor=CommandExecutor(()),
    )


def canonical_openapi(app: FastAPI) -> str:
    return (
        json.dumps(app.openapi(), allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def write_openapi(path: Path = OPENAPI_ARTIFACT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_openapi(contract_app()), encoding="utf-8")


def check_openapi(path: Path = OPENAPI_ARTIFACT) -> bool:
    try:
        committed = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    return committed == canonical_openapi(contract_app())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--output", type=Path, default=OPENAPI_ARTIFACT)
    options = parser.parse_args(argv)
    if options.write:
        write_openapi(options.output)
        return 0
    return 0 if check_openapi(options.output) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "OPENAPI_ARTIFACT",
    "canonical_openapi",
    "check_openapi",
    "contract_app",
    "main",
    "write_openapi",
]
