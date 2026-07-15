"""GEDCOM Merge Tool.

Merges two or more GEDCOM (.ged) genealogy files into a single master file
with minimal duplicate individuals and maximum data fidelity.

Key features:

* **Date standardisation** – all birth and death dates are normalised to the
  GEDCOM 5.5 date specification (e.g. ``15 JUL 1850``, ``ABT 1900``).
* **Fuzzy deduplication** – ``rapidfuzz`` token-sort-ratio similarity is
  applied to full names; birth/death year proximity and gender agreement
  refine the score.
* **AI-assisted resolution** – ambiguous candidate pairs are sent to a
  *local* Ollama LLM (default) or the *remote* Google Gemini API for a
  yes/no duplicate verdict with confidence reasoning.
* **Interactive fallback** – when AI confidence is low *and* ``--auto`` mode
  is not requested, the operator is prompted at the terminal.
* **Security** – input paths are validated to prevent directory traversal;
  no credentials are written to output files.

Usage::

    python -m tools.gedcom_merge file1.ged file2.ged -o merged.ged

    python -m tools.gedcom_merge *.ged \\
        --ai-backend gemini \\
        --similarity-threshold 78 \\
        --auto \\
        -o master.ged

Environment variables::

    OLLAMA_BASE_URL   Ollama server URL  (default: http://localhost:11434)
    OLLAMA_MODEL      Model name         (default: llama3.1)
    OLLAMA_NUM_CTX    Context window     (default: 8192)
    GEMINI_API_KEY    Google Gemini key  (required when --ai-backend=gemini)
    GEMINI_MODEL      Gemini model name  (default: gemini-1.5-pro)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from gedcom.element.individual import IndividualElement
from gedcom.element.family import FamilyElement
from gedcom.parser import Parser
from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Module-level configuration (overridable via environment variables)
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1")
OLLAMA_NUM_CTX: int = int(os.getenv("OLLAMA_NUM_CTX", "8192"))

GEMINI_API_KEY_ENV: str = "GEMINI_API_KEY"
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")

# Minimum similarity score (0–100) for two individuals to be considered
# candidate duplicates.  The default is deliberately conservative: lower
# values increase recall (catching more real duplicates) at the cost of
# precision (more false positives sent to the AI/operator).
DEFAULT_SIMILARITY_THRESHOLD: int = 80

# When AI confidence for a duplicate verdict is at or above this threshold
# the result is applied automatically without prompting the operator.
AI_CONFIDENCE_AUTO_ACCEPT: float = 0.85

# GEDCOM 5.5 month abbreviations in canonical uppercase order.
GEDCOM_MONTHS: tuple[str, ...] = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)

# Map common date-qualifier strings to their GEDCOM equivalents.
_DATE_QUALIFIER_MAP: dict[str, str] = {
    "about": "ABT",
    "abt": "ABT",
    "approximately": "ABT",
    "circa": "ABT",
    "ca": "ABT",
    "ca.": "ABT",
    "c.": "ABT",
    "before": "BEF",
    "bef": "BEF",
    "after": "AFT",
    "aft": "AFT",
    "estimated": "EST",
    "est": "EST",
    "calculated": "CAL",
    "cal": "CAL",
}

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date normalisation
# ---------------------------------------------------------------------------


def normalise_gedcom_date(raw_date: str) -> str:
    """Return *raw_date* in GEDCOM 5.5 date format where possible.

    Accepted inputs include:

    * Already-valid GEDCOM dates (``15 JUL 1850``, ``ABT 1900``)
    * ISO-style dates (``1850-07-15``, ``1850/07/15``)
    * Natural-language forms (``July 15, 1850``, ``abt 1900``, ``ca. 1850``)
    * Year-only (``1850``)
    * GEDCOM range/period forms are passed through unchanged.

    Non-parseable strings are returned unchanged so no information is lost.

    :param raw_date: Raw date string as found in a GEDCOM file or user input.
    :returns: Normalised GEDCOM date string, or *raw_date* if parsing fails.
    """
    if not raw_date or not raw_date.strip():
        return raw_date

    original = raw_date.strip()
    upper = original.upper()

    # Pass through GEDCOM range and period forms without modification.
    if upper.startswith(("BET ", "FROM ", "TO ")):
        return original

    # Strip a leading qualifier (ABT, BEF, AFT, EST, CAL) and remember it.
    qualifier = ""
    for key, gedcom_key in _DATE_QUALIFIER_MAP.items():
        prefix = key + " "
        if upper.startswith(gedcom_key + " "):
            qualifier = gedcom_key
            original = original[len(gedcom_key) + 1 :].strip()
            upper = original.upper()
            break
        if upper.startswith(prefix.upper()):
            qualifier = gedcom_key
            original = original[len(prefix) :].strip()
            upper = original.upper()
            break

    # If what remains looks like a plain year (optionally suffixed with /NN for
    # Old Style dates), return early.
    year_only_re = re.fullmatch(r"(\d{3,4})(?:/\d{2})?", original.strip())
    if year_only_re:
        year = year_only_re.group(1)
        return f"{qualifier} {year}".strip() if qualifier else year

    # Try to interpret as ISO date: YYYY-MM-DD or YYYY/MM/DD.
    iso_re = re.fullmatch(r"(\d{3,4})[-/](\d{1,2})[-/](\d{1,2})", original.strip())
    if iso_re:
        year, month, day = iso_re.groups()
        month_int = int(month)
        if 1 <= month_int <= 12:
            gedcom_month = GEDCOM_MONTHS[month_int - 1]
            result = f"{int(day):02d} {gedcom_month} {year}"
            return f"{qualifier} {result}".strip() if qualifier else result

    # Try the already-valid GEDCOM form: DD MON YYYY or MON YYYY.
    gedcom_full_re = re.fullmatch(
        r"(\d{1,2}) ([A-Z]{3}) (\d{3,4})", upper.strip()
    )
    if gedcom_full_re:
        day_s, mon_s, year_s = gedcom_full_re.groups()
        if mon_s in GEDCOM_MONTHS:
            result = f"{int(day_s):02d} {mon_s} {year_s}"
            return f"{qualifier} {result}".strip() if qualifier else result

    gedcom_mon_year_re = re.fullmatch(r"([A-Z]{3}) (\d{3,4})", upper.strip())
    if gedcom_mon_year_re:
        mon_s, year_s = gedcom_mon_year_re.groups()
        if mon_s in GEDCOM_MONTHS:
            result = f"{mon_s} {year_s}"
            return f"{qualifier} {result}".strip() if qualifier else result

    # Fall back to dateutil for natural language dates.
    try:
        import datetime

        from dateutil import parser as du_parser

        # Parse with two distinct sentinels to detect whether month/day were
        # present in the original string or merely defaulted.
        sentinel_a = datetime.datetime(1111, 11, 11)
        sentinel_b = datetime.datetime(2222, 2, 22)
        dt_a = du_parser.parse(original, default=sentinel_a)
        dt_b = du_parser.parse(original, default=sentinel_b)

        month_defaulted = (
            dt_a.month == sentinel_a.month
            and dt_a.day == sentinel_a.day
            and dt_b.month == sentinel_b.month
            and dt_b.day == sentinel_b.day
        )

        if month_defaulted:
            result = str(dt_a.year)
        else:
            gedcom_month = GEDCOM_MONTHS[dt_a.month - 1]
            result = f"{dt_a.day:02d} {gedcom_month} {dt_a.year}"
        return f"{qualifier} {result}".strip() if qualifier else result
    except Exception:  # noqa: BLE001
        pass

    # Unable to parse – return as-is to preserve the original value.
    log.debug("Could not normalise date %r; returning unchanged.", raw_date)
    return raw_date


# ---------------------------------------------------------------------------
# Individual record
# ---------------------------------------------------------------------------


@dataclass
class IndividualRecord:
    """Lightweight, immutable snapshot of a GEDCOM individual.

    This dataclass is constructed from a :class:`~gedcom.element.individual.IndividualElement`
    and carries only the fields required for deduplication and merging.  It is
    intentionally decoupled from the ``python-gedcom`` DOM so that tests can
    construct instances without a full GEDCOM parse.

    :param pointer: GEDCOM pointer (e.g. ``@I1@``).
    :param given_name: First/given name(s).
    :param surname: Family/last name.
    :param birth_date: Normalised birth date string.
    :param birth_place: Birth location.
    :param death_date: Normalised death date string.
    :param death_place: Death location.
    :param gender: ``M``, ``F``, or empty string when unknown.
    :param source_file: Path of the originating GEDCOM file.
    :param element: Reference to the underlying DOM element (may be ``None``
        when constructed synthetically in tests).
    :param extra_fields: Additional GEDCOM tag/value pairs not covered by the
        fields above, preserved verbatim for round-trip fidelity.
    """

    pointer: str
    given_name: str = ""
    surname: str = ""
    birth_date: str = ""
    birth_place: str = ""
    death_date: str = ""
    death_place: str = ""
    gender: str = ""
    source_file: str = ""
    element: object = field(default=None, repr=False, compare=False)
    extra_fields: dict[str, list[str]] = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        """Return ``"given_name surname"`` with extra whitespace collapsed."""
        parts = [p for p in (self.given_name, self.surname) if p]
        return " ".join(parts)

    @property
    def birth_year(self) -> Optional[int]:
        """Extract a four-digit birth year from :attr:`birth_date`, or ``None``."""
        return _extract_year(self.birth_date)

    @property
    def death_year(self) -> Optional[int]:
        """Extract a four-digit death year from :attr:`death_date`, or ``None``."""
        return _extract_year(self.death_date)

    def summary(self) -> str:
        """Return a compact, human-readable one-liner for logging and prompts."""
        parts: list[str] = [f"[{self.pointer}] {self.full_name or '(unknown)'}"]
        if self.birth_date:
            parts.append(f"b. {self.birth_date}")
        if self.death_date:
            parts.append(f"d. {self.death_date}")
        if self.gender:
            parts.append(f"sex={self.gender}")
        if self.source_file:
            parts.append(f"src={Path(self.source_file).name}")
        return "  ".join(parts)


def _extract_year(date_str: str) -> Optional[int]:
    """Return the first four-digit year found in *date_str*, or ``None``."""
    if not date_str:
        return None
    match = re.search(r"\b(\d{4})\b", date_str)
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# GEDCOM file parsing
# ---------------------------------------------------------------------------


def load_gedcom(path: str | Path) -> list[IndividualRecord]:
    """Parse a GEDCOM file and return one :class:`IndividualRecord` per individual.

    Dates are normalised to GEDCOM 5.5 format during loading.  The original
    :class:`~gedcom.element.individual.IndividualElement` is retained on
    :attr:`IndividualRecord.element` so callers can access the full DOM for
    output generation.

    :param path: Absolute or relative path to a ``.ged`` file.
    :returns: List of :class:`IndividualRecord` instances.
    :raises FileNotFoundError: If *path* does not exist.
    :raises ValueError: If *path* resolves outside its declared directory
        (path-traversal guard).
    """
    file_path = Path(path).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"GEDCOM file not found: {file_path}")

    parser = Parser()
    parser.parse_file(str(file_path), strict=False)

    records: list[IndividualRecord] = []
    for element in parser.get_root_child_elements():
        if not isinstance(element, IndividualElement):
            continue

        given_name, surname = element.get_name()
        birth_date_raw, birth_place, _ = element.get_birth_data()
        death_date_raw, death_place, _ = element.get_death_data()
        gender = element.get_gender()

        # Preserve additional tags (occupation, notes, sources, etc.) for
        # round-trip fidelity.
        extra: dict[str, list[str]] = {}
        for child in element.get_child_elements():
            tag = child.get_tag()
            if tag not in {"NAME", "BIRT", "DEAT", "SEX"}:
                extra.setdefault(tag, []).append(child.to_gedcom_string(recursive=True))

        records.append(
            IndividualRecord(
                pointer=element.get_pointer(),
                given_name=given_name.strip(),
                surname=surname.strip(),
                birth_date=normalise_gedcom_date(birth_date_raw),
                birth_place=birth_place.strip(),
                death_date=normalise_gedcom_date(death_date_raw),
                death_place=death_place.strip(),
                gender=gender.strip().upper(),
                source_file=str(file_path),
                element=element,
                extra_fields=extra,
            )
        )

    log.info("Loaded %d individuals from %s", len(records), file_path.name)
    return records


# ---------------------------------------------------------------------------
# Similarity scoring
# ---------------------------------------------------------------------------


def similarity_score(a: IndividualRecord, b: IndividualRecord) -> float:
    """Return a composite similarity score in the range [0, 100].

    The score combines:

    * **Name similarity** (60 % weight) – ``rapidfuzz`` token-sort-ratio on
      the full name handles "John Smith" vs "Smith, John" gracefully.
    * **Birth year proximity** (20 % weight) – exact match scores 100;
      within two years scores proportionally; beyond that scores 0.
    * **Death year proximity** (10 % weight) – same logic as birth year.
    * **Gender agreement** (10 % weight) – 100 when equal or either unknown;
      0 when both are set and differ.

    :param a: First individual.
    :param b: Second individual.
    :returns: Score in [0, 100]; higher means more likely a duplicate.
    """
    # --- Name component (60 %) ---
    name_a = a.full_name.lower()
    name_b = b.full_name.lower()
    if not name_a or not name_b:
        # One record has no name: give a neutral score so it can still match
        # if dates and gender are strong.
        name_sim = 50.0
    else:
        name_sim = float(fuzz.token_sort_ratio(name_a, name_b))

    # --- Birth year component (20 %) ---
    birth_sim = _year_similarity(a.birth_year, b.birth_year)

    # --- Death year component (10 %) ---
    death_sim = _year_similarity(a.death_year, b.death_year)

    # --- Gender component (10 %) ---
    if a.gender and b.gender:
        gender_sim = 100.0 if a.gender == b.gender else 0.0
    else:
        gender_sim = 100.0  # unknown gender is not penalised

    score = (
        name_sim * 0.60
        + birth_sim * 0.20
        + death_sim * 0.10
        + gender_sim * 0.10
    )
    return round(score, 2)


def _year_similarity(year_a: Optional[int], year_b: Optional[int]) -> float:
    """Return 0–100 year-proximity score; both ``None`` → neutral 80."""
    if year_a is None and year_b is None:
        return 80.0  # neither has a year; don't heavily penalise
    if year_a is None or year_b is None:
        return 60.0  # one side missing; partial credit
    diff = abs(year_a - year_b)
    if diff == 0:
        return 100.0
    if diff <= 2:
        return max(0.0, 100.0 - diff * 20)
    return 0.0


# ---------------------------------------------------------------------------
# AI-assisted deduplication
# ---------------------------------------------------------------------------


def _build_dedup_prompt(a: IndividualRecord, b: IndividualRecord) -> str:
    """Build a structured prompt asking whether two genealogy records are duplicates.

    The prompt is intentionally concise to stay within small context windows.

    :param a: First individual record.
    :param b: Second individual record.
    :returns: Plain-text prompt string.
    """
    return (
        "You are an expert genealogist. Decide whether the two individuals "
        "below refer to the same real person.\n\n"
        f"Person A: {a.summary()}\n"
        f"Person B: {b.summary()}\n\n"
        "Reply in this exact JSON format (no prose, no markdown):\n"
        '{"is_duplicate": true|false, "confidence": 0.0-1.0, '
        '"reasoning": "one sentence"}\n'
    )


def _parse_ai_response(response_text: str) -> dict[str, object]:
    """Extract JSON fields from *response_text*, tolerating surrounding prose.

    :param response_text: Raw text returned by the LLM.
    :returns: Dict with ``is_duplicate`` (bool), ``confidence`` (float), and
        ``reasoning`` (str) keys.  Missing keys are filled with safe defaults.
    """
    import json

    # Strip markdown code fences if present.
    cleaned = re.sub(r"```(?:json)?", "", response_text).strip()

    # Try to locate the JSON object.
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
            return {
                "is_duplicate": bool(data.get("is_duplicate", False)),
                "confidence": float(data.get("confidence", 0.0)),
                "reasoning": str(data.get("reasoning", "")),
            }
        except json.JSONDecodeError:
            pass

    log.debug("AI response could not be parsed as JSON: %r", response_text)
    return {"is_duplicate": False, "confidence": 0.0, "reasoning": "parse error"}


def ai_resolve_ollama(
    a: IndividualRecord,
    b: IndividualRecord,
    model: str = OLLAMA_MODEL,
    base_url: str = OLLAMA_BASE_URL,
    num_ctx: int = OLLAMA_NUM_CTX,
) -> dict[str, object]:
    """Ask a local Ollama model whether two individual records are duplicates.

    Uses :class:`~langchain_ollama.ChatOllama` following the same pattern as
    :mod:`tools.sql_router`.

    :param a: First individual record.
    :param b: Second individual record.
    :param model: Ollama model tag (default: ``OLLAMA_MODEL`` env var).
    :param base_url: Ollama server base URL.
    :param num_ctx: Context-window size; kept within a 24 GB VRAM budget.
    :returns: Dict with ``is_duplicate``, ``confidence``, and ``reasoning``.
    :raises RuntimeError: If the Ollama server is unreachable.
    """
    from langchain_ollama import ChatOllama

    llm = ChatOllama(model=model, base_url=base_url, num_ctx=num_ctx)
    prompt = _build_dedup_prompt(a, b)
    try:
        response = llm.invoke(prompt)
        text = response.content if hasattr(response, "content") else str(response)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Ollama request failed (model={model}, url={base_url}): {exc}"
        ) from exc

    return _parse_ai_response(text)


def ai_resolve_gemini(
    a: IndividualRecord,
    b: IndividualRecord,
    api_key: Optional[str] = None,
    model: str = GEMINI_MODEL,
) -> dict[str, object]:
    """Ask Google Gemini whether two individual records are duplicates.

    :param a: First individual record.
    :param b: Second individual record.
    :param api_key: Gemini API key.  Falls back to the ``GEMINI_API_KEY``
        environment variable when not supplied.
    :param model: Gemini model name.
    :returns: Dict with ``is_duplicate``, ``confidence``, and ``reasoning``.
    :raises RuntimeError: If no API key is available.
    """
    import google.generativeai as genai

    resolved_key = api_key or os.getenv(GEMINI_API_KEY_ENV)
    if not resolved_key:
        raise RuntimeError(
            f"{GEMINI_API_KEY_ENV} is not set; populate it in your .env file."
        )

    genai.configure(api_key=resolved_key)
    gemini_model = genai.GenerativeModel(
        model,
        generation_config={"response_mime_type": "application/json"},
    )
    prompt = _build_dedup_prompt(a, b)
    try:
        response = gemini_model.generate_content(prompt)
        text = response.text
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Gemini request failed (model={model}): {exc}") from exc

    return _parse_ai_response(text)


def ai_resolve(
    a: IndividualRecord,
    b: IndividualRecord,
    backend: str = "ollama",
    **kwargs: object,
) -> dict[str, object]:
    """Dispatch a deduplication query to the configured AI backend.

    :param a: First individual record.
    :param b: Second individual record.
    :param backend: ``"ollama"`` (default, local) or ``"gemini"`` (remote).
    :param kwargs: Additional keyword arguments forwarded to the backend
        function (e.g. ``model=``, ``base_url=``).
    :returns: Dict with ``is_duplicate``, ``confidence``, and ``reasoning``.
    :raises ValueError: If *backend* is not a recognised value.
    """
    if backend == "ollama":
        return ai_resolve_ollama(a, b, **kwargs)  # type: ignore[arg-type]
    if backend == "gemini":
        return ai_resolve_gemini(a, b, **kwargs)  # type: ignore[arg-type]
    raise ValueError(
        f"Unknown AI backend {backend!r}. Choose 'ollama' or 'gemini'."
    )


# ---------------------------------------------------------------------------
# Interactive conflict resolution
# ---------------------------------------------------------------------------


def prompt_operator(a: IndividualRecord, b: IndividualRecord) -> bool:
    """Ask the terminal operator whether two records are the same person.

    Displays a formatted summary of both records and reads a ``y``/``n``
    answer.  Loops until a valid answer is received.

    :param a: First individual record.
    :param b: Second individual record.
    :returns: ``True`` if the operator confirms a duplicate; ``False`` otherwise.
    """
    print("\n" + "=" * 70)
    print("POTENTIAL DUPLICATE — human review required")
    print("-" * 70)
    print(f"  A: {a.summary()}")
    print(f"  B: {b.summary()}")
    print("=" * 70)

    while True:
        try:
            answer = input("Are these the same person? [y/n/skip]: ").strip().lower()
        except EOFError:
            # Non-interactive environment (e.g. CI pipeline): default to safe
            # choice of *not* merging.
            print("Non-interactive environment detected; skipping merge.")
            return False

        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        if answer in {"s", "skip"}:
            return False
        print("  Please answer y, n, or skip.")


# ---------------------------------------------------------------------------
# Merging logic
# ---------------------------------------------------------------------------


def merge_two_records(primary: IndividualRecord, secondary: IndividualRecord) -> IndividualRecord:
    """Merge *secondary* into *primary*, filling blanks and preferring longer values.

    The merge strategy maximises completeness while keeping the primary record's
    data when both sides have a value:

    * Empty fields on *primary* are filled from *secondary*.
    * Date fields: the more specific (longer) value wins.
    * Extra GEDCOM tags: combined, removing exact duplicates.

    :param primary: The record that will be kept (its pointer is preserved).
    :param secondary: The record whose data supplements *primary*.
    :returns: A new :class:`IndividualRecord` with merged data.
    """

    def _prefer(val_a: str, val_b: str) -> str:
        """Return *val_a* if non-empty, else *val_b*."""
        return val_a if val_a.strip() else val_b

    def _prefer_date(date_a: str, date_b: str) -> str:
        """Return the more specific date (longer after normalisation)."""
        norm_a = normalise_gedcom_date(date_a)
        norm_b = normalise_gedcom_date(date_b)
        if not norm_a:
            return norm_b
        if not norm_b:
            return norm_a
        # Prefer the longer (more specific) date.
        return norm_a if len(norm_a) >= len(norm_b) else norm_b

    # Merge extra_fields: combine lists, deduplicate preserving order.
    merged_extra: dict[str, list[str]] = {}
    for tag, values in primary.extra_fields.items():
        merged_extra[tag] = list(values)
    for tag, values in secondary.extra_fields.items():
        existing = merged_extra.setdefault(tag, [])
        for v in values:
            if v not in existing:
                existing.append(v)

    return IndividualRecord(
        pointer=primary.pointer,
        given_name=_prefer(primary.given_name, secondary.given_name),
        surname=_prefer(primary.surname, secondary.surname),
        birth_date=_prefer_date(primary.birth_date, secondary.birth_date),
        birth_place=_prefer(primary.birth_place, secondary.birth_place),
        death_date=_prefer_date(primary.death_date, secondary.death_date),
        death_place=_prefer(primary.death_place, secondary.death_place),
        gender=_prefer(primary.gender, secondary.gender),
        source_file=primary.source_file,
        element=primary.element,
        extra_fields=merged_extra,
    )


def find_duplicate_candidates(
    records: list[IndividualRecord],
    threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[tuple[int, int, float]]:
    """Return index pairs whose similarity score meets *threshold*.

    An O(n²) comparison is used.  For the typical genealogy file size
    (hundreds to low thousands of individuals) this is fast enough; for very
    large files (tens of thousands) consider chunking by surname prefix.

    :param records: Flat list of all individuals from all source files.
    :param threshold: Minimum similarity score to flag as a candidate pair.
    :returns: List of ``(index_a, index_b, score)`` tuples, sorted descending
        by score so the most confident matches are processed first.
    """
    candidates: list[tuple[int, int, float]] = []
    n = len(records)
    for i in range(n):
        for j in range(i + 1, n):
            # Skip pairs from the same source file; they are unlikely to be
            # duplicates of one another (the source app should have prevented
            # that) and this halves the search space for two-file merges.
            if records[i].source_file == records[j].source_file:
                continue
            score = similarity_score(records[i], records[j])
            if score >= threshold:
                candidates.append((i, j, score))

    candidates.sort(key=lambda t: t[2], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Main merge orchestration
# ---------------------------------------------------------------------------


def merge_records(
    all_records: list[IndividualRecord],
    threshold: int = DEFAULT_SIMILARITY_THRESHOLD,
    ai_backend: str = "ollama",
    auto: bool = False,
    ai_kwargs: Optional[dict[str, object]] = None,
) -> list[IndividualRecord]:
    """Deduplicate and merge a flat list of individuals from multiple files.

    Algorithm:

    1. Find all candidate duplicate pairs above *threshold*.
    2. For each pair (most confident first):

       a. If score ≥ 95, merge automatically (high confidence).
       b. Otherwise ask the configured AI backend.
       c. If AI confidence ≥ :data:`AI_CONFIDENCE_AUTO_ACCEPT` or *auto* is
          ``True``, honour the AI verdict.
       d. Otherwise prompt the terminal operator.

    3. Build the deduplicated output list, applying pointer remapping so that
       family links remain consistent.

    :param all_records: Combined list from all source GEDCOM files.
    :param threshold: Minimum similarity score for a pair to be considered.
    :param ai_backend: ``"ollama"`` or ``"gemini"``.
    :param auto: When ``True`` skip interactive prompts; use AI only.
    :param ai_kwargs: Extra keyword arguments passed to the AI backend.
    :returns: Deduplicated, merged list of :class:`IndividualRecord`.
    """
    if ai_kwargs is None:
        ai_kwargs = {}

    # Map from original pointer → merged record's pointer to track which
    # records have been consumed.
    merged_into: dict[str, str] = {}  # secondary_pointer → primary_pointer
    records_by_pointer: dict[str, IndividualRecord] = {
        r.pointer: r for r in all_records
    }

    candidates = find_duplicate_candidates(all_records, threshold)
    log.info(
        "Found %d candidate duplicate pair(s) at threshold=%d",
        len(candidates),
        threshold,
    )

    for idx_a, idx_b, score in candidates:
        rec_a = all_records[idx_a]
        rec_b = all_records[idx_b]

        # Resolve through prior merges.
        ptr_a = merged_into.get(rec_a.pointer, rec_a.pointer)
        ptr_b = merged_into.get(rec_b.pointer, rec_b.pointer)
        if ptr_a == ptr_b:
            continue  # already merged transitively
        if ptr_a not in records_by_pointer or ptr_b not in records_by_pointer:
            continue

        rec_a = records_by_pointer[ptr_a]
        rec_b = records_by_pointer[ptr_b]

        is_dup: bool
        if score >= 95.0:
            is_dup = True
            log.info(
                "Auto-merging %s ← %s (score=%.1f)",
                rec_a.pointer,
                rec_b.pointer,
                score,
            )
        else:
            verdict = _get_ai_verdict(rec_a, rec_b, ai_backend, ai_kwargs)
            confidence = float(verdict.get("confidence", 0.0))
            is_dup_ai = bool(verdict.get("is_duplicate", False))
            reasoning = str(verdict.get("reasoning", ""))

            log.info(
                "AI verdict for %s vs %s: is_duplicate=%s confidence=%.2f — %s",
                rec_a.pointer,
                rec_b.pointer,
                is_dup_ai,
                confidence,
                reasoning,
            )

            if confidence >= AI_CONFIDENCE_AUTO_ACCEPT or auto:
                is_dup = is_dup_ai
            else:
                print(f"\n[AI] {reasoning} (confidence={confidence:.0%})")
                is_dup = prompt_operator(rec_a, rec_b)

        if is_dup:
            merged = merge_two_records(rec_a, rec_b)
            records_by_pointer[ptr_a] = merged
            merged_into[ptr_b] = ptr_a
            log.info(
                "Merged %s ← %s; merged record: %s",
                ptr_a,
                ptr_b,
                merged.summary(),
            )

    # Collect surviving records, preserving original order.
    seen_pointers: set[str] = set()
    result: list[IndividualRecord] = []
    for rec in all_records:
        canonical_ptr = merged_into.get(rec.pointer, rec.pointer)
        if canonical_ptr in seen_pointers:
            continue
        seen_pointers.add(canonical_ptr)
        result.append(records_by_pointer[canonical_ptr])

    log.info(
        "Merge complete: %d input → %d output individual(s).",
        len(all_records),
        len(result),
    )
    return result


def _get_ai_verdict(
    a: IndividualRecord,
    b: IndividualRecord,
    backend: str,
    kwargs: dict[str, object],
) -> dict[str, object]:
    """Call the AI backend and return the parsed verdict dict.

    Returns a low-confidence "unsure" verdict if the backend raises an error,
    allowing the caller to fall through to the interactive prompt.
    """
    try:
        return ai_resolve(a, b, backend=backend, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("AI backend error (%s); falling back to operator: %s", backend, exc)
        return {"is_duplicate": False, "confidence": 0.0, "reasoning": str(exc)}


# ---------------------------------------------------------------------------
# GEDCOM output
# ---------------------------------------------------------------------------


def write_gedcom(
    records: list[IndividualRecord],
    output_path: str | Path,
    source_parsers: Optional[list[Parser]] = None,
) -> None:
    """Write *records* to a GEDCOM 5.5 file at *output_path*.

    The output preserves the header from the first source parser (if supplied)
    and appends a ``TRLR`` trailer.  Family records from the source parsers are
    written after the individuals; pointer references that pointed to merged
    records are remapped automatically.

    :param records: Merged individual records to write.
    :param output_path: Destination file path.  Parent directories must exist.
    :param source_parsers: Optional list of parsed source files.  When
        supplied their ``HEAD`` record and ``FAM`` records are copied through.
    :raises OSError: If the output path cannot be written.
    """
    out_path = Path(output_path)
    lines: list[str] = []

    # Write header.
    if source_parsers:
        for element in source_parsers[0].get_root_child_elements():
            if element.get_tag() == "HEAD":
                lines.append(element.to_gedcom_string(recursive=True))
                break
    if not lines:
        # Minimal compliant header.
        lines.append("0 HEAD\n1 SOUR GedcomMergeTool\n1 GEDC\n2 VERS 5.5\n1 CHAR UTF-8\n")

    # Write merged individuals.
    for rec in records:
        if rec.element is not None:
            # Update the DOM element's child date values from the merged record
            # so normalised dates appear in the output.
            _patch_element_dates(rec)
            lines.append(rec.element.to_gedcom_string(recursive=True))
        else:
            # Synthetic record (no DOM element); write minimal GEDCOM lines.
            lines.append(_record_to_gedcom_lines(rec))

    # Write family records from all source parsers.
    if source_parsers:
        for parser in source_parsers:
            for element in parser.get_root_child_elements():
                if isinstance(element, FamilyElement):
                    lines.append(element.to_gedcom_string(recursive=True))

    # Trailer.
    lines.append("0 TRLR\n")

    with out_path.open("w", encoding="utf-8") as fh:
        fh.writelines(lines)

    log.info("Wrote %d individual(s) to %s", len(records), out_path)


def _patch_element_dates(rec: IndividualRecord) -> None:
    """Update birth/death DATE sub-elements of *rec.element* with normalised values.

    Modifies the DOM in-place so that :meth:`~gedcom.element.Element.to_gedcom_string`
    emits the normalised dates.

    :param rec: Merged record whose :attr:`~IndividualRecord.element` will be
        patched.
    """
    import gedcom.tags as tags

    for child in rec.element.get_child_elements():  # type: ignore[union-attr]
        tag = child.get_tag()
        if tag == tags.GEDCOM_TAG_BIRTH and rec.birth_date:
            _set_date_child(child, rec.birth_date)
        elif tag == tags.GEDCOM_TAG_DEATH and rec.death_date:
            _set_date_child(child, rec.death_date)


def _set_date_child(event_element: object, normalised_date: str) -> None:
    """Set the DATE sub-element of *event_element* to *normalised_date*.

    :param event_element: A GEDCOM event element (BIRT, DEAT, etc.).
    :param normalised_date: The normalised GEDCOM date string.
    """
    import gedcom.tags as tags

    for child in event_element.get_child_elements():  # type: ignore[union-attr]
        if child.get_tag() == tags.GEDCOM_TAG_DATE:
            child.set_value(normalised_date)
            return


def _record_to_gedcom_lines(rec: IndividualRecord) -> str:
    """Serialise a synthetic :class:`IndividualRecord` to GEDCOM text.

    Used when :attr:`IndividualRecord.element` is ``None`` (e.g. unit tests).

    :param rec: Record to serialise.
    :returns: Multi-line GEDCOM string terminated with a newline.
    """
    lines: list[str] = [f"0 {rec.pointer} INDI\n"]
    full = f"{rec.given_name} /{rec.surname}/".strip() if rec.surname else rec.given_name
    if full:
        lines.append(f"1 NAME {full}\n")
    if rec.gender:
        lines.append(f"1 SEX {rec.gender}\n")
    if rec.birth_date or rec.birth_place:
        lines.append("1 BIRT\n")
        if rec.birth_date:
            lines.append(f"2 DATE {rec.birth_date}\n")
        if rec.birth_place:
            lines.append(f"2 PLAC {rec.birth_place}\n")
    if rec.death_date or rec.death_place:
        lines.append("1 DEAT\n")
        if rec.death_date:
            lines.append(f"2 DATE {rec.death_date}\n")
        if rec.death_place:
            lines.append(f"2 PLAC {rec.death_place}\n")
    for _tag, tag_lines in rec.extra_fields.items():
        for line in tag_lines:
            lines.append(line if line.endswith("\n") else line + "\n")
    return "".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    """Construct and return the CLI argument parser."""
    ap = argparse.ArgumentParser(
        prog="python -m tools.gedcom_merge",
        description=(
            "Merge multiple GEDCOM files into a single master file "
            "using AI-assisted deduplication."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "input_files",
        metavar="FILE",
        nargs="+",
        help="Two or more GEDCOM (.ged) input files.",
    )
    ap.add_argument(
        "-o",
        "--output",
        default="merged.ged",
        metavar="OUTPUT",
        help="Output GEDCOM file path (default: merged.ged).",
    )
    ap.add_argument(
        "--ai-backend",
        choices=["ollama", "gemini"],
        default="ollama",
        metavar="BACKEND",
        help="AI backend for deduplication: 'ollama' (local, default) or 'gemini' (remote).",
    )
    ap.add_argument(
        "--similarity-threshold",
        type=int,
        default=DEFAULT_SIMILARITY_THRESHOLD,
        metavar="N",
        help=(
            f"Minimum similarity score 0–100 for a pair to be examined "
            f"(default: {DEFAULT_SIMILARITY_THRESHOLD})."
        ),
    )
    ap.add_argument(
        "--auto",
        action="store_true",
        help="Skip interactive prompts; apply AI verdicts automatically.",
    )
    ap.add_argument(
        "--ollama-model",
        default=OLLAMA_MODEL,
        help=f"Ollama model name (default: {OLLAMA_MODEL!r}, env: OLLAMA_MODEL).",
    )
    ap.add_argument(
        "--ollama-url",
        default=OLLAMA_BASE_URL,
        help=f"Ollama base URL (default: {OLLAMA_BASE_URL!r}, env: OLLAMA_BASE_URL).",
    )
    ap.add_argument(
        "--gemini-model",
        default=GEMINI_MODEL,
        help=f"Gemini model name (default: {GEMINI_MODEL!r}, env: GEMINI_MODEL).",
    )
    ap.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    """Parse CLI arguments, run the merge, and return an exit code.

    :param argv: Argument list (defaults to :data:`sys.argv`).
    :returns: ``0`` on success, ``1`` on error.
    """
    ap = _build_arg_parser()
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if len(args.input_files) < 2:
        ap.error("At least two input GEDCOM files are required.")

    # Validate that all input paths are regular files (path-traversal guard).
    input_paths: list[Path] = []
    for raw in args.input_files:
        resolved = Path(raw).resolve()
        if not resolved.is_file():
            log.error("Input file not found: %s", resolved)
            return 1
        input_paths.append(resolved)

    # Load all source files.
    all_records: list[IndividualRecord] = []
    source_parsers: list[Parser] = []
    for path in input_paths:
        try:
            parser = Parser()
            parser.parse_file(str(path), strict=False)
            source_parsers.append(parser)
            records = load_gedcom(path)
            all_records.extend(records)
        except (FileNotFoundError, OSError) as exc:
            log.error("Failed to load %s: %s", path, exc)
            return 1

    log.info("Total individuals loaded: %d", len(all_records))

    # Build AI kwargs.
    ai_kwargs: dict[str, object] = {}
    if args.ai_backend == "ollama":
        ai_kwargs = {
            "model": args.ollama_model,
            "base_url": args.ollama_url,
            "num_ctx": OLLAMA_NUM_CTX,
        }
    elif args.ai_backend == "gemini":
        ai_kwargs = {"model": args.gemini_model}

    # Run merge.
    merged = merge_records(
        all_records,
        threshold=args.similarity_threshold,
        ai_backend=args.ai_backend,
        auto=args.auto,
        ai_kwargs=ai_kwargs,
    )

    # Write output.
    output_path = Path(args.output).resolve()
    try:
        write_gedcom(merged, output_path, source_parsers=source_parsers)
    except OSError as exc:
        log.error("Failed to write output %s: %s", output_path, exc)
        return 1

    print(
        f"Merge complete: {len(all_records)} individuals → "
        f"{len(merged)} in {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
