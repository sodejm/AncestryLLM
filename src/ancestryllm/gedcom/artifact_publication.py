"""Atomic publication adapter for rendered GEDCOM text artifacts."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ancestryllm.core.publication import (
    StagedFileToken,
    claim_staged_path,
    cleanup_staged_path,
    is_staging_path,
    publish_staged_bundle,
    staging_path,
    write_staged_text,
)


def stage_text_atomically(path: Path, payload: str) -> StagedFileToken:
    """Write through a publication-owned reservation without clobbering it."""
    if is_staging_path(path):
        return write_staged_text(path, payload)
    staged = staging_path(path)
    try:
        token = write_staged_text(staged, payload)
        claim_staged_path(staged, token)
        publish_staged_bundle(((staged, path),), replace=os.replace)
        return token
    except BaseException:
        cleanup_staged_path(staged)
        raise


__all__ = ["stage_text_atomically"]
