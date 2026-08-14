#!/usr/bin/env python3
"""Validate and normalize deterministic documentation screenshot plans."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import struct
import sys
import zlib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from markdown_it import MarkdownIt

if __package__:
    from scripts.docs_linking import source_anchors, split_destination
else:
    from docs_linking import source_anchors, split_destination

SCHEMA_VERSION = 1
_MANIFEST_SCHEMA = "config/docs-screenshot-manifest-v1.schema.json"
_FIXTURE_SCHEMA = "config/docs-screenshot-fixture-v1.schema.json"
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_SHELL_METACHARACTERS = re.compile(r"[|;&><`]|\$\(")
_FORBIDDEN_LAUNCHERS = frozenset(
    {
        "bash",
        "cmd",
        "cmd.exe",
        "curl",
        "fish",
        "powershell",
        "powershell.exe",
        "pwsh",
        "sh",
        "wget",
        "zsh",
    }
)
_REQUIRED_FIXTURE_STATES = frozenset({"success", "degraded", "privacy-canary"})
_GENERIC_ALT_TEXT = frozenset({"figure", "image", "photo", "picture", "screenshot"})
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_DECODE_LIMIT = 256 * 1024 * 1024
_PNG_COLOR_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_PNG_BIT_DEPTHS = {
    0: frozenset({1, 2, 4, 8, 16}),
    2: frozenset({8, 16}),
    3: frozenset({1, 2, 4, 8}),
    4: frozenset({8, 16}),
    6: frozenset({8, 16}),
}
_ADAM7_PASSES = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


class ScreenshotManifestError(ValueError):
    """A screenshot-contract failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ValidatedManifest:
    """Validated screenshot plan plus privacy canaries kept out of plan output."""

    payload: dict[str, Any]
    privacy_canaries: tuple[str, ...]

    @property
    def schema_version(self) -> int:
        """Return the validated schema version."""
        return int(self.payload["schema_version"])

    @property
    def fixtures(self) -> tuple[dict[str, Any], ...]:
        """Return the validated fixture descriptors."""
        return tuple(self.payload["fixtures"])

    @property
    def scenarios(self) -> tuple[dict[str, Any], ...]:
        """Return the validated capture scenarios."""
        return tuple(self.payload["scenarios"])


def _fail(code: str, message: str) -> None:
    raise ScreenshotManifestError(code, message)


def _png_fail() -> None:
    _fail("DOCSHOT_ASSET_INVALID", "a declared screenshot asset is not a valid PNG")


def _png_pass_size(length: int, start: int, step: int) -> int:
    return 0 if length <= start else (length - start + step - 1) // step


def _png_scanline_layouts(
    *,
    width: int,
    height: int,
    bits_per_pixel: int,
    interlace: int,
) -> tuple[tuple[int, int], ...]:
    if interlace == 0:
        return ((height, (width * bits_per_pixel + 7) // 8),)
    layouts: list[tuple[int, int]] = []
    for start_x, start_y, step_x, step_y in _ADAM7_PASSES:
        pass_width = _png_pass_size(width, start_x, step_x)
        pass_height = _png_pass_size(height, start_y, step_y)
        if pass_width and pass_height:
            layouts.append((pass_height, (pass_width * bits_per_pixel + 7) // 8))
    return tuple(layouts)


def validate_png_bytes(content: bytes) -> None:
    """Parse and decode a complete PNG, rejecting malformed or trailing data."""
    if not content.startswith(_PNG_SIGNATURE):
        _png_fail()

    cursor = len(_PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    saw_header = saw_palette = saw_image_data = saw_end = False
    image_data_closed = False
    image_data = bytearray()

    while cursor < len(content):
        if len(content) - cursor < 12:
            _png_fail()
        chunk_length = struct.unpack_from(">I", content, cursor)[0]
        chunk_type = content[cursor + 4 : cursor + 8]
        payload_start = cursor + 8
        payload_end = payload_start + chunk_length
        chunk_end = payload_end + 4
        if (
            len(chunk_type) != 4
            or not all(
                ord("A") <= octet <= ord("Z") or ord("a") <= octet <= ord("z")
                for octet in chunk_type
            )
            or chunk_type[2] & 0x20
            or chunk_end > len(content)
        ):
            _png_fail()
        payload = content[payload_start:payload_end]
        recorded_crc = struct.unpack_from(">I", content, payload_end)[0]
        if recorded_crc != zlib.crc32(chunk_type + payload) & 0xFFFFFFFF:
            _png_fail()
        cursor = chunk_end

        if not saw_header and chunk_type != b"IHDR":
            _png_fail()
        if chunk_type == b"IHDR":
            if saw_header or chunk_length != 13:
                _png_fail()
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if (
                width == 0
                or height == 0
                or color_type not in _PNG_BIT_DEPTHS
                or bit_depth not in _PNG_BIT_DEPTHS[color_type]
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                _png_fail()
            saw_header = True
            continue
        if chunk_type == b"PLTE":
            if (
                saw_palette
                or saw_image_data
                or color_type in {0, 4}
                or chunk_length == 0
                or chunk_length > 768
                or chunk_length % 3
                or (color_type == 3 and chunk_length // 3 > 2**bit_depth)
            ):
                _png_fail()
            saw_palette = True
            continue
        if chunk_type == b"IDAT":
            if image_data_closed or (color_type == 3 and not saw_palette):
                _png_fail()
            saw_image_data = True
            if len(image_data) + chunk_length > _PNG_DECODE_LIMIT:
                _png_fail()
            image_data.extend(payload)
            continue
        if saw_image_data:
            image_data_closed = True
        if chunk_type == b"IEND":
            if not saw_image_data or chunk_length != 0 or cursor != len(content):
                _png_fail()
            saw_end = True
            break
        if chunk_type[0] & 0x20 == 0:
            _png_fail()

    if not saw_end or None in {width, height, bit_depth, color_type, interlace}:
        _png_fail()
    assert width is not None
    assert height is not None
    assert bit_depth is not None
    assert color_type is not None
    assert interlace is not None

    channels = _PNG_COLOR_CHANNELS[color_type]
    layouts = _png_scanline_layouts(
        width=width,
        height=height,
        bits_per_pixel=channels * bit_depth,
        interlace=interlace,
    )
    decoded_size = sum(rows * (row_bytes + 1) for rows, row_bytes in layouts)
    if decoded_size <= 0 or decoded_size > _PNG_DECODE_LIMIT:
        _png_fail()
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(bytes(image_data), decoded_size + 1)
        if decoder.unconsumed_tail or len(decoded) > decoded_size:
            _png_fail()
        decoded += decoder.flush(decoded_size + 1 - len(decoded))
    except zlib.error:
        _png_fail()
    if (
        not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
        or len(decoded) != decoded_size
    ):
        _png_fail()
    cursor = 0
    for rows, row_bytes in layouts:
        for _row in range(rows):
            if decoded[cursor] > 4:
                _png_fail()
            cursor += row_bytes + 1
    if cursor != len(decoded):
        _png_fail()


def _read_json(path: Path, code: str, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code, f"{label} is missing or is not valid UTF-8 JSON")


def _validate_schema(instance: Any, schema: Any, code: str, label: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    except SchemaError:
        _fail(code, f"{label} schema is invalid")
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "root"
        _fail(code, f"{label} does not satisfy its closed schema at {location}")


def _safe_repository_path(
    raw_path: str,
    *,
    repository_root: Path,
    prefix: str,
    suffix: str,
    code: str,
) -> Path:
    if (
        not raw_path
        or "\\" in raw_path
        or raw_path.startswith("/")
        or _WINDOWS_DRIVE.match(raw_path)
        or any(part in {"", ".", ".."} for part in raw_path.split("/"))
    ):
        _fail(code, "path must be a normalized repository-relative path")

    relative = PurePosixPath(raw_path)
    required_prefix = PurePosixPath(prefix)
    if (
        relative.parts[: len(required_prefix.parts)] != required_prefix.parts
        or relative.suffix.casefold() != suffix.casefold()
    ):
        _fail(code, f"path must stay below {prefix} and use the {suffix} suffix")

    resolved_root = repository_root.resolve()
    candidate = repository_root.joinpath(*relative.parts)
    current = repository_root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            _fail(code, "symlinked path components are not permitted")
    try:
        resolved_candidate = candidate.resolve(strict=False)
    except OSError:
        _fail(code, "path could not be resolved safely")
    if not resolved_candidate.is_relative_to(resolved_root):
        _fail(code, "path escapes the repository root")
    return candidate


def _validate_launch_allowlist(payload: dict[str, Any]) -> frozenset[str]:
    allowlist = frozenset(payload["launch_allowlist"])
    for executable in allowlist:
        basename = PurePosixPath(executable).name.casefold()
        if (
            basename in _FORBIDDEN_LAUNCHERS
            or _URL.search(executable)
            or _SHELL_METACHARACTERS.search(executable)
        ):
            _fail("DOCSHOT_LAUNCH_UNSAFE", "launch allowlist contains an unsafe executable")
    return allowlist


def _validate_launch(launch: list[str], allowlist: frozenset[str]) -> None:
    executable = launch[0]
    if executable not in allowlist:
        _fail("DOCSHOT_LAUNCH_UNSAFE", "scenario executable is not allowlisted")
    for token in launch:
        if (
            "\x00" in token
            or "\n" in token
            or "\r" in token
            or _URL.search(token)
            or _SHELL_METACHARACTERS.search(token)
        ):
            _fail("DOCSHOT_LAUNCH_UNSAFE", "scenario launch contains shell or URL syntax")


def _validate_fixture_descriptors(
    payload: dict[str, Any],
    *,
    repository_root: Path,
    fixture_schema: Any,
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    fixture_by_id: dict[str, dict[str, Any]] = {}
    casefolded_ids: set[str] = set()
    canaries: list[str] = []

    for descriptor in payload["fixtures"]:
        fixture_id = descriptor["id"]
        normalized_id = fixture_id.casefold()
        if normalized_id in casefolded_ids:
            _fail("DOCSHOT_FIXTURE_ID_DUPLICATE", "fixture IDs must be case-insensitively unique")
        casefolded_ids.add(normalized_id)

        if descriptor["provider"] != "none":
            _fail("DOCSHOT_PROVIDER_NOT_NONE", "documentation fixtures must use provider none")
        if descriptor["network"] != "disabled":
            _fail("DOCSHOT_NETWORK_NOT_DENIED", "documentation fixture network must be disabled")
        if descriptor["fictional"] is not True:
            _fail("DOCSHOT_FIXTURE_NOT_FICTIONAL", "documentation fixtures must be fictional")

        fixture_path = _safe_repository_path(
            descriptor["path"],
            repository_root=repository_root,
            prefix="tests/fixtures/docs_screenshots",
            suffix=".json",
            code="DOCSHOT_FIXTURE_PATH_UNSAFE",
        )
        fixture = _read_json(
            fixture_path,
            "DOCSHOT_FIXTURE_SCHEMA_INVALID",
            f"fixture {fixture_id}",
        )
        _validate_schema(
            fixture,
            fixture_schema,
            "DOCSHOT_FIXTURE_SCHEMA_INVALID",
            f"fixture {fixture_id}",
        )
        expected_identity = {
            "fixture_id": fixture_id,
            "state": descriptor["state"],
            "provider": descriptor["provider"],
            "network": descriptor["network"],
            "fictional": descriptor["fictional"],
        }
        if any(fixture[field] != value for field, value in expected_identity.items()):
            _fail("DOCSHOT_FIXTURE_MISMATCH", "fixture identity differs from its descriptor")

        if fixture["provider"] != "none":
            _fail("DOCSHOT_PROVIDER_NOT_NONE", "fixture document must use provider none")
        if fixture["network"] != "disabled":
            _fail("DOCSHOT_NETWORK_NOT_DENIED", "fixture document network must be disabled")
        if fixture["fictional"] is not True:
            _fail("DOCSHOT_FIXTURE_NOT_FICTIONAL", "fixture document must be fictional")

        serialized_content = json.dumps(fixture["content"], ensure_ascii=False, sort_keys=True)
        for canary in fixture["privacy_canaries"]:
            if canary not in serialized_content:
                _fail(
                    "DOCSHOT_PRIVACY_CANARY_INVALID",
                    "privacy canary must occur in the validation-only fixture content",
                )
            canaries.append(canary)
        fixture_by_id[fixture_id] = fixture

    states = {fixture["state"] for fixture in fixture_by_id.values()}
    if states != _REQUIRED_FIXTURE_STATES:
        _fail(
            "DOCSHOT_FIXTURE_STATE_INCOMPLETE",
            "success, degraded, and privacy-canary fixture states are required",
        )
    return fixture_by_id, tuple(sorted(canaries))


def _validate_scenarios(
    payload: dict[str, Any],
    *,
    repository_root: Path,
    fixture_by_id: dict[str, dict[str, Any]],
) -> None:
    launch_allowlist = _validate_launch_allowlist(payload)
    scenario_ids: set[str] = set()
    output_paths: set[str] = set()
    declared_outputs: set[str] = set()

    for scenario in payload["scenarios"]:
        normalized_id = scenario["id"].casefold()
        if normalized_id in scenario_ids:
            _fail(
                "DOCSHOT_SCENARIO_ID_DUPLICATE",
                "scenario IDs must be case-insensitively unique",
            )
        scenario_ids.add(normalized_id)

        normalized_output = scenario["output_path"].casefold()
        if normalized_output in output_paths:
            _fail(
                "DOCSHOT_OUTPUT_DUPLICATE",
                "scenario destinations must be case-insensitively unique",
            )
        output_paths.add(normalized_output)
        _safe_repository_path(
            scenario["output_path"],
            repository_root=repository_root,
            prefix="docs/assets/screenshots",
            suffix=".png",
            code="DOCSHOT_OUTPUT_PATH_UNSAFE",
        )
        declared_outputs.add(scenario["output_path"])

        _validate_launch(scenario["launch"], launch_allowlist)
        expected_geometry = "viewport" if scenario["surface"] == "electron" else "terminal"
        if scenario["geometry"]["kind"] != expected_geometry:
            _fail(
                "DOCSHOT_GEOMETRY_MISMATCH",
                "scenario geometry does not match its capture surface",
            )

        fixture_id = scenario["fixture_id"]
        fixture = fixture_by_id.get(fixture_id)
        if fixture is None:
            _fail("DOCSHOT_FIXTURE_NOT_FOUND", "scenario references an unknown fixture")
        if fixture["state"] == "privacy-canary":
            _fail(
                "DOCSHOT_PRIVACY_FIXTURE_REFERENCED",
                "privacy-canary fixtures cannot be used by publishable scenarios",
            )

        for reference in scenario["documentation"]:
            documentation_path = _safe_repository_path(
                reference["path"],
                repository_root=repository_root,
                prefix="docs",
                suffix=".md",
                code="DOCSHOT_DOC_PATH_UNSAFE",
            )
            if not documentation_path.is_file():
                _fail("DOCSHOT_DOC_NOT_FOUND", "referenced documentation file does not exist")
            try:
                markdown = documentation_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                _fail("DOCSHOT_DOC_NOT_FOUND", "referenced documentation file is unreadable")
            if reference["anchor"] not in source_anchors(markdown):
                _fail(
                    "DOCSHOT_DOC_ANCHOR_MISSING",
                    "referenced documentation anchor does not exist",
                )

    output_allowlist = payload["output_allowlist"]
    normalized_allowlist = [output.casefold() for output in output_allowlist]
    if len(set(normalized_allowlist)) != len(normalized_allowlist):
        _fail(
            "DOCSHOT_OUTPUT_DUPLICATE",
            "output allowlist destinations must be case-insensitively unique",
        )
    allowlisted_outputs = set(output_allowlist)
    for output_path in allowlisted_outputs:
        _safe_repository_path(
            output_path,
            repository_root=repository_root,
            prefix="docs/assets/screenshots",
            suffix=".png",
            code="DOCSHOT_OUTPUT_PATH_UNSAFE",
        )
    if declared_outputs != allowlisted_outputs:
        _fail(
            "DOCSHOT_OUTPUT_ALLOWLIST_MISMATCH",
            "scenario destinations must exactly match the output allowlist",
        )


def validate_manifest(
    payload: dict[str, Any],
    *,
    repository_root: Path,
    schema_path: Path,
    fixture_schema_path: Path,
) -> ValidatedManifest:
    """Validate a screenshot manifest and every referenced fixture and document."""
    if type(payload) is not dict or type(payload.get("schema_version")) is not int:
        _fail("DOCSHOT_SCHEMA_UNSUPPORTED", "manifest schema_version must be integer 1")
    if payload["schema_version"] != SCHEMA_VERSION:
        _fail("DOCSHOT_SCHEMA_UNSUPPORTED", "manifest schema_version is not supported")

    manifest_schema = _read_json(schema_path, "DOCSHOT_SCHEMA_INVALID", "manifest schema")
    fixture_schema = _read_json(
        fixture_schema_path,
        "DOCSHOT_FIXTURE_SCHEMA_INVALID",
        "fixture schema",
    )
    _validate_schema(payload, manifest_schema, "DOCSHOT_SCHEMA_INVALID", "manifest")
    fixture_by_id, canaries = _validate_fixture_descriptors(
        payload,
        repository_root=repository_root,
        fixture_schema=fixture_schema,
    )
    _validate_scenarios(
        payload,
        repository_root=repository_root,
        fixture_by_id=fixture_by_id,
    )
    return ValidatedManifest(payload=deepcopy(payload), privacy_canaries=canaries)


def load_manifest(path: Path, *, repository_root: Path) -> ValidatedManifest:
    """Load and validate the schema-v1 manifest from a repository root."""
    manifest_path = path if path.is_absolute() else repository_root / path
    payload = _read_json(manifest_path, "DOCSHOT_SCHEMA_INVALID", "manifest")
    if type(payload) is not dict:
        _fail("DOCSHOT_SCHEMA_UNSUPPORTED", "manifest root must be an object")
    return validate_manifest(
        payload,
        repository_root=repository_root,
        schema_path=repository_root / _MANIFEST_SCHEMA,
        fixture_schema_path=repository_root / _FIXTURE_SCHEMA,
    )


def normalized_plan_json(manifest: ValidatedManifest) -> str:
    """Serialize a deterministic capture plan without privacy-canary fixture content."""
    referenced_fixture_ids = {scenario["fixture_id"] for scenario in manifest.scenarios}
    plan = {
        "schema_version": manifest.schema_version,
        "determinism": manifest.payload["determinism"],
        "fixtures": sorted(
            (fixture for fixture in manifest.fixtures if fixture["id"] in referenced_fixture_ids),
            key=lambda fixture: fixture["id"],
        ),
        "scenarios": sorted(manifest.scenarios, key=lambda scenario: scenario["id"]),
    }
    return json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def validate_capture_text(manifest: ValidatedManifest, text: str) -> None:
    """Fail closed when textual capture evidence contains a privacy canary."""
    if any(canary in text for canary in manifest.privacy_canaries):
        _fail(
            "DOCSHOT_PRIVACY_CANARY_LEAKED",
            "capture text contains an unpublishable privacy canary",
        )


def _resolved_markdown_image(
    documentation_path: PurePosixPath,
    destination: str,
) -> str | None:
    target, _title = split_destination(destination.strip())
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    try:
        decoded = unquote(parsed.path, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        _fail("DOCSHOT_DOC_IMAGE_UNDECLARED", "screenshot image target is not valid UTF-8")
    if "\\" in decoded or decoded.startswith("/") or _WINDOWS_DRIVE.match(decoded):
        _fail("DOCSHOT_DOC_IMAGE_UNDECLARED", "screenshot image target is unsafe")
    resolved = posixpath.normpath((documentation_path.parent / decoded).as_posix())
    if resolved == ".." or resolved.startswith("../"):
        _fail("DOCSHOT_DOC_IMAGE_UNDECLARED", "screenshot image target escapes docs")
    if not resolved.startswith("docs/assets/screenshots/"):
        return None
    return resolved


def _meaningful_alt_text(alt_text: str, output_path: str) -> bool:
    normalized = " ".join(alt_text.split()).strip()
    if len(normalized) < 12 or len(normalized.split()) < 2:
        return False
    casefolded = normalized.casefold()
    words = tuple(re.findall(r"[\w]+", casefolded))
    if words and all(word in _GENERIC_ALT_TEXT for word in words):
        return False
    stem = PurePosixPath(output_path).stem.replace("-", " ").replace("_", " ").casefold()
    return casefolded != stem


def _rendered_markdown_images(markdown: str) -> tuple[tuple[str, str], ...]:
    images: list[tuple[str, str]] = []

    def collect(tokens: list[Any] | None) -> None:
        for token in tokens or []:
            if token.type == "image":
                images.append((token.content, token.attrGet("src") or ""))
            collect(token.children)

    collect(MarkdownIt("commonmark").parse(markdown))
    return tuple(images)


def validate_published_assets(
    manifest: ValidatedManifest,
    *,
    repository_root: Path,
) -> None:
    """Validate declared PNG ownership and every Markdown screenshot reference."""
    resolved_repository = repository_root.resolve()
    declared_outputs = {str(scenario["output_path"]) for scenario in manifest.scenarios}
    allowed_references = {
        (str(reference["path"]), str(scenario["output_path"]))
        for scenario in manifest.scenarios
        for reference in scenario["documentation"]
    }

    for output_path in sorted(declared_outputs):
        output = _safe_repository_path(
            output_path,
            repository_root=resolved_repository,
            prefix="docs/assets/screenshots",
            suffix=".png",
            code="DOCSHOT_OUTPUT_PATH_UNSAFE",
        )
        if output.is_symlink() or not output.is_file():
            _fail("DOCSHOT_ASSET_MISSING", "a declared screenshot asset is missing")
        try:
            content = output.read_bytes()
        except OSError:
            _fail("DOCSHOT_ASSET_MISSING", "a declared screenshot asset is unreadable")
        if any(canary.encode("utf-8") in content for canary in manifest.privacy_canaries):
            _fail("DOCSHOT_PRIVACY_CANARY_LEAKED", "a published PNG contains a privacy canary")
        validate_png_bytes(content)

    screenshot_root = resolved_repository / "docs/assets/screenshots"
    if screenshot_root.is_symlink():
        _fail("DOCSHOT_ASSET_ORPHANED", "the screenshot root cannot be a symlink")
    discovered_outputs = (
        {
            path.relative_to(resolved_repository).as_posix()
            for path in screenshot_root.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        if screenshot_root.is_dir()
        else set()
    )
    if discovered_outputs - declared_outputs:
        _fail("DOCSHOT_ASSET_ORPHANED", "an unowned screenshot asset is published")

    discovered_references: set[tuple[str, str]] = set()
    docs_root = resolved_repository / "docs"
    for documentation in sorted(docs_root.rglob("*.md")):
        if documentation.is_symlink() or not documentation.is_file():
            continue
        relative_documentation = PurePosixPath(
            documentation.relative_to(resolved_repository).as_posix(),
        )
        try:
            markdown = documentation.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            _fail("DOCSHOT_DOC_NOT_FOUND", "documentation screenshot owner is unreadable")
        for alt_text, destination in _rendered_markdown_images(markdown):
            resolved_image = _resolved_markdown_image(
                relative_documentation,
                destination,
            )
            if resolved_image is None:
                continue
            if resolved_image not in declared_outputs:
                _fail(
                    "DOCSHOT_DOC_IMAGE_UNDECLARED",
                    "Markdown references an undeclared screenshot asset",
                )
            if not _meaningful_alt_text(alt_text, resolved_image):
                _fail("DOCSHOT_DOC_ALT_INVALID", "screenshot alt text is not meaningful")
            discovered_references.add((relative_documentation.as_posix(), resolved_image))

    undeclared_references = discovered_references - allowed_references
    if undeclared_references:
        _fail(
            "DOCSHOT_DOC_IMAGE_UNDECLARED",
            "a screenshot embedding is absent from the ownership manifest",
        )
    if allowed_references - discovered_references:
        _fail(
            "DOCSHOT_DOC_IMAGE_MISSING",
            "a manifest-owned documentation file does not embed its screenshot",
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "validate"))
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("config/docs-screenshot-manifest.json"),
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the validation-only documentation screenshot contract CLI."""
    args = _parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest, repository_root=args.repository_root)
    except ScreenshotManifestError as error:
        print(error.code, file=sys.stderr)
        return 2
    if args.command == "plan":
        print(normalized_plan_json(manifest), end="")
    else:
        try:
            validate_published_assets(manifest, repository_root=args.repository_root)
        except ScreenshotManifestError as error:
            print(error.code, file=sys.stderr)
            return 2
        print('{"schema_version":1,"status":"valid"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
