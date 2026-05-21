"""Source Intake — paste normalisation."""

import pytest

from api.pipeline.source_intake import ingest_paste


def test_strips_trailing_whitespace_and_normalises_newlines():
    out = ingest_paste("hello \r\nworld\t \n\n\n\nmore\n")
    assert out.body == "hello\nworld\n\nmore"
    assert out.char_count == len(out.body)


def test_decodes_bytes():
    out = ingest_paste(b"hi there")
    assert out.body == "hi there"


def test_rejects_empty_paste():
    with pytest.raises(ValueError):
        ingest_paste("\n\n   \t\n")


def test_collapses_runs_of_blank_lines():
    text = "a\n\n\n\n\nb"
    assert ingest_paste(text).body == "a\n\nb"


def test_nfc_normalises_combining_characters():
    # "é" composed vs decomposed.
    composed = "café"
    out = ingest_paste(composed)
    assert out.body == "café"
