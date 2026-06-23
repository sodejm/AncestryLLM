from unittest.mock import patch

import tools.gemini_transcription as gemini


def test_normalize_strips_whitespace_duplicates_and_non_ascii():
    raw = "  John   Smith \n\nJohn Smith\nBorn 1820 \u2014 caf\u00e9\n\n"

    result = gemini.normalize_transcription(raw)

    assert result == "John Smith\nBorn 1820 cafe"


def test_normalize_handles_empty_input():
    assert gemini.normalize_transcription("") == ""


def test_map_transcription_requires_api_key():
    with patch.dict("os.environ", {}, clear=True):
        try:
            gemini.map_transcription("anything", api_key=None)
        except RuntimeError as exc:
            assert gemini.GEMINI_API_KEY_ENV in str(exc)
        else:
            raise AssertionError("Expected RuntimeError when API key is missing")
