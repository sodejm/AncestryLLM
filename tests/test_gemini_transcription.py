from ancestryllm.ocr import service as gemini


def test_normalize_strips_whitespace_duplicates_and_non_ascii():
    raw = "  John   Smith \n\nJohn Smith\nBorn 1820 \u2014 caf\u00e9\n\n"

    result = gemini.normalize_transcription(raw)

    assert result == "John Smith\nBorn 1820 cafe"


def test_normalize_handles_empty_input():
    assert gemini.normalize_transcription("") == ""
