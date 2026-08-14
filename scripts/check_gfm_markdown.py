"""Apply deterministic GFM structural checks to every tracked Markdown document."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path


class MarkdownValidationError(ValueError):
    """A Markdown validation failure with a stable machine-readable code."""

    def __init__(self, code: str, path: Path, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code}: {path.as_posix()}: {detail}")


def _reject_control_characters(path: Path, markdown: str) -> None:
    line = 1
    column = 0
    for character in markdown:
        if character == "\n":
            line += 1
            column = 0
            continue
        column += 1
        if character not in {"\r", "\t"} and unicodedata.category(character) == "Cc":
            raise MarkdownValidationError(
                "GFM_CONTROL_CHARACTER",
                path,
                f"disallowed U+{ord(character):04X} at line {line}, column {column}",
            )


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return None
    candidate = line[indent:]
    if not candidate or candidate[0] not in {"`", "~"}:
        return None
    marker = candidate[0]
    length = len(candidate) - len(candidate.lstrip(marker))
    if length < 3:
        return None
    return marker, length, candidate[length:]


def _reject_unclosed_fences(path: Path, markdown: str) -> None:
    opening: tuple[str, int, int] | None = None
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        fence = _fence_marker(line)
        if fence is None:
            continue
        marker, length, remainder = fence
        if opening is None:
            if marker == "`" and "`" in remainder:
                continue
            opening = marker, length, line_number
            continue
        opening_marker, opening_length, _ = opening
        if marker == opening_marker and length >= opening_length and not remainder.strip():
            opening = None

    if opening is not None:
        marker, length, line_number = opening
        raise MarkdownValidationError(
            "GFM_UNCLOSED_FENCE",
            path,
            f"{marker * length} opened at line {line_number} has no closing fence",
        )


def validate_markdown(path: Path, markdown: str) -> None:
    """Validate one document with the repository's deterministic GFM contract."""

    _reject_control_characters(path, markdown)
    _reject_unclosed_fences(path, markdown)


def tracked_markdown_paths(root: Path) -> list[Path]:
    """Return every tracked Markdown path under *root* in stable order."""

    git = shutil.which("git")
    if git is None:
        raise MarkdownValidationError("GFM_GIT_NOT_FOUND", Path(), "git executable was not found")
    try:
        result = subprocess.run(  # noqa: S603 - fixed executable and arguments
            [git, "ls-files", "-z", "--", "*.md"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise MarkdownValidationError(
            "GFM_GIT_LIST_FAILED",
            Path(),
            f"git ls-files exited with status {exc.returncode}",
        ) from exc

    try:
        relative_paths = [
            Path(entry.decode("utf-8")) for entry in result.stdout.split(b"\0") if entry
        ]
    except UnicodeDecodeError as exc:
        raise MarkdownValidationError(
            "GFM_PATH_ENCODING", Path(), "tracked path is not valid UTF-8"
        ) from exc
    return [root / path for path in sorted(relative_paths, key=lambda item: item.as_posix())]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply GFM structural checks to all Git-tracked Markdown files."
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the check gfm markdown command and return its exit status."""
    args = _parse_args(argv)
    root = args.root.resolve()
    try:
        paths = tracked_markdown_paths(root)
        for path in paths:
            relative_path = path.relative_to(root)
            try:
                markdown = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise MarkdownValidationError(
                    "GFM_DOCUMENT_ENCODING",
                    relative_path,
                    "document is not valid UTF-8",
                ) from exc
            except OSError as exc:
                raise MarkdownValidationError(
                    "GFM_DOCUMENT_READ_FAILED",
                    relative_path,
                    f"document could not be read: {type(exc).__name__}",
                ) from exc
            validate_markdown(relative_path, markdown)
    except MarkdownValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"GFM_MARKDOWN_OK: files={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
