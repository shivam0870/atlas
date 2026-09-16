import pytest

from atlas.chunking import split_text
from atlas.generation import citations_valid
from atlas.retrieval import rrf


def test_chunk_spans_and_token_safety():
    text = "First section.\n\n" + "unusuallylongtoken " * 200 + "🪴 final sentence."
    chunks = split_text(text, 100, 20, lambda s: len(s), max_tokens=60)
    assert chunks
    for chunk in chunks:
        assert chunk.text == text[chunk.start : chunk.end]
        assert len(chunk.text) <= 60
    covered = set()
    for c in chunks:
        covered.update(range(c.start, c.end))
    assert all(i in covered for i, char in enumerate(text) if not char.isspace())


def test_overlap_validation():
    with pytest.raises(ValueError):
        split_text("abc", 10, 10)
    assert split_text("  ") == []


def test_rrf_actual_math():
    scores = rrf(["a", "b"], ["b", "c"])
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["c"] == pytest.approx(1 / 62)


def test_citation_validation():
    assert citations_valid("A fact [1]. Another [2].", 2)
    assert not citations_valid("An invented source [3].", 2)
    assert not citations_valid("No citation", 2)
