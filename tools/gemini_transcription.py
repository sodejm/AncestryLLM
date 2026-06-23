from __future__ import annotations

import os
import unicodedata
from typing import Optional

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"

# Concise system instruction: minimizes prompt token overhead while strictly
# enforcing a structured, minified JSON output.
GENEALOGY_SYSTEM_INSTRUCTION = (
    "Extract genealogy facts from OCR text. "
    "Return only minified JSON matching "
    '{"people":[{"name":str,"birth":str|null,"death":str|null,"relations":[str]}]}. '
    "No prose. No markdown."
)


def normalize_transcription(text: str) -> str:
    """Strip whitespace, duplicate lines, and non-ASCII OCR artifacts.

    Pre-processing OCR text before sending it to the Gemini API trims token
    usage and removes noise that wastes paid input tokens.
    """
    if not text:
        return ""

    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")

    cleaned_lines: list[str] = []
    seen_lines: set[str] = set()
    for raw_line in ascii_text.splitlines():
        line = " ".join(raw_line.split())
        if not line or line in seen_lines:
            continue
        seen_lines.add(line)
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


def map_transcription(raw_text: str, api_key: Optional[str] = None) -> str:
    """Send a pre-trimmed OCR transcription to Gemini and return JSON text."""
    api_key = api_key or os.getenv(GEMINI_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"{GEMINI_API_KEY_ENV} is not set; populate it in your .env file."
        )

    cleaned_text = normalize_transcription(raw_text)

    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        system_instruction=GENEALOGY_SYSTEM_INSTRUCTION,
        generation_config={"response_mime_type": "application/json"},
    )
    response = model.generate_content(cleaned_text)
    return response.text
