from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    start: int
    end: int
    text: str


def split_text(
    text: str,
    size: int = 1000,
    overlap: int = 120,
    token_count: Callable[[str], int] | None = None,
    max_tokens: int = 480,
) -> list[Chunk]:
    """Prefer natural boundaries, retaining original character spans exactly.

    Character targets are not token limits. Oversized fragments are shortened by
    binary search using the actual embedding tokenizer, never silently truncated.
    """
    if size < 2 or overlap < 0 or overlap >= size:
        raise ValueError("Chunk overlap must be nonnegative and less than size")
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            for separator in ["\n\n", "\n", ". ", " "]:
                boundary = text.rfind(separator, start + size // 2, end)
                if boundary > start:
                    end = boundary + len(separator)
                    break
        if token_count and token_count(text[start:end]) > max_tokens:
            lo, hi = start + 1, end
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if token_count(text[start:mid]) <= max_tokens:
                    lo = mid
                else:
                    hi = mid - 1
            end = lo
        if text[start:end].strip():
            chunks.append(Chunk(start, end, text[start:end]))
        if end == len(text):
            break
        start = max(start + 1, end - min(overlap, (end - start) // 2))
    return chunks
