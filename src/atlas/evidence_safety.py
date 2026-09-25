"""Conservative passage verification, not a model-based truth or entailment score.

Only literal supported claims are accepted. This deliberately declines paraphrases
that a similarity score or another generative model might incorrectly approve.
"""

import re
from dataclasses import dataclass

VERIFIER_REVISION = "literal-passages-v2"
MARKER = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class Verification:
    supported: bool
    claims: int
    rejected: int
    method: str = VERIFIER_REVISION


def normalized(text: str) -> str:
    # Do not remove negations, digits, signs, or comparison operators.
    return re.sub(r"\s+", " ", text.replace("\u2019", "'")).strip().casefold()


def passage_units(content: str) -> set[str]:
    return {
        normalized(re.sub(r"^(?:[-*>]\s*|\d+[.)]\s+)", "", part.strip())).rstrip(".")
        for part in re.split(r"(?<=[.!?])\s+|\n+", content)
        if part.strip()
    }


def verify_answer(answer: str, sources: list[dict]) -> Verification:
    paragraphs = [part.strip() for part in answer.splitlines() if part.strip()]
    evidence = [passage_units(str(source.get("content", ""))) for source in sources]
    rejected = 0
    claims = 0
    for paragraph in paragraphs:
        references = [int(value) for value in MARKER.findall(paragraph)]
        # A citation supports the entire paragraph, including every sentence in it.
        body = MARKER.sub("", paragraph).strip()
        body = re.sub(r"^(?:[-*>]\s*|\d+[.)]\s+)", "", body).strip()
        body = body.strip('"\u201c\u201d')
        body = re.sub(r"\s+([.,;:!?])", r"\1", body)
        parts = [item.strip(' \t"\u201c\u201d') for item in re.split(r"(?<=[.!?])\s+", body)]
        for part in parts:
            if not part:
                continue
            claims += 1
            claim = normalized(part).rstrip(".")
            valid = references and all(1 <= ref <= len(sources) for ref in references)
            supported = (
                valid
                and len(claim) >= 3
                # A real but unrelated reference is a misleading citation. Every
                # citation attached to the paragraph must support every claim in
                # it; claims backed by different sources belong in separate
                # paragraphs, as requested by the generation prompt.
                and all(claim in evidence[ref - 1] for ref in references)
            )
            if not supported:
                rejected += 1
    return Verification(bool(claims) and not rejected, claims, rejected)


def evidence_fallback(sources: list[dict], unavailable=False) -> str:
    reason = (
        "Generation is temporarily unavailable. Authorized source excerpts are available below."
        if unavailable
        else "I could not verify an answer against the cited passages. These source excerpts may help; please refine your question if they do not answer it."
    )
    # Do not copy instructions from an untrusted passage into the answer. Excerpts
    # remain in the separately rendered Sources panel, with exact version links.
    return reason if sources else "I could not find enough evidence to answer this question."


def flag_possible_conflicts(sources: list[dict]) -> None:
    """Flag matching statements with differing quantities; never infer a winner."""
    seen: dict[str, list[tuple[dict, tuple[str, ...]]]] = {}
    for source in sources:
        source["possible_conflict"] = False
        source["conflicts_with"] = []
        for sentence in passage_units(str(source.get("content", ""))):
            values = tuple(re.findall(r"\b\d+(?:\.\d+)?\b", sentence))
            if not values or len(sentence.split()) < 4:
                continue
            key = re.sub(r"\b\d+(?:\.\d+)?\b", "<quantity>", sentence)
            for other, other_values in seen.get(key, []):
                if other.get("document_id") == source.get("document_id") or values == other_values:
                    continue
                source["possible_conflict"] = other["possible_conflict"] = True
                source["conflicts_with"].append(other["id"])
                other["conflicts_with"].append(source["id"])
            seen.setdefault(key, []).append((source, values))
