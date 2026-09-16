import json

import pytest

from atlas.evaluation import label_hash, regression_gate, retrieval_metrics


def test_metrics_known_ranking():
    result = retrieval_metrics(["wrong", "correct", "other"], {"correct"})
    assert result["recall_at_5"] == 1
    assert result["mrr"] == 0.5
    assert result["ndcg_at_5"] == pytest.approx(1 / 1.584962500721156)
    assert retrieval_metrics(["wrong"], {"correct"})["recall_at_5"] == 0


def test_no_gold_cannot_be_scored():
    with pytest.raises(ValueError):
        retrieval_metrics(["x"], set())


def test_edited_question_invalidates_review():
    row = {
        "question": "original",
        "document_id": "d",
        "source_hash": "h",
        "start_offset": 0,
        "end_offset": 20,
        "split": "development",
    }
    reviewed = label_hash(row)
    row["question"] = "changed"
    assert label_hash(row) != reviewed


def test_regression_gate_rejects_degraded_quality(tmp_path):
    # This is a synthetic unit-test threshold, never a committed quality claim.
    path = tmp_path / "threshold.json"
    path.write_text(json.dumps({"recall_at_5": 0.8}))
    regression_gate({"recall_at_5": 0.9}, path)
    with pytest.raises(ValueError, match="regressed"):
        regression_gate({"recall_at_5": 0.2}, path)
