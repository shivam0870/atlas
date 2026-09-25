"""Citation accuracy regressions: existing references must actually support claims."""

import pytest

from atlas.evidence_safety import VERIFIER_REVISION, verify_answer


@pytest.mark.parametrize(
    "answer",
    [
        "Release approval is required. [1][2]",
        "Release approval is required [1] [2].",
        "Release approval is required. [2] The quota is 100. [1]",
    ],
)
def test_existing_but_misleading_citations_are_rejected(answer):
    sources = [{"content": "Release approval is required."}, {"content": "The quota is 100."}]
    assert not verify_answer(answer, sources).supported


def test_separate_claims_keep_their_own_evidence():
    sources = [{"content": "Release approval is required."}, {"content": "The quota is 100."}]
    assert verify_answer(
        "Release approval is required. [1]\nThe quota is 100. [2]", sources
    ).supported


def test_corroborating_citations_both_support_the_claim():
    sources = [{"content": "Release approval is required."}] * 2
    assert verify_answer("Release approval is required. [1][2]", sources).supported
    assert VERIFIER_REVISION == "literal-passages-v2"
