from __future__ import annotations

import json
from pathlib import Path

import pytest

from agiwiki.codec import file_sha256
from tools.evaluate_fragment_retrieval import EvaluationError, evaluate_fragments


def test_fragment_baseline_binds_source_and_measures_evidence_recall(
    tmp_path: Path,
) -> None:
    source = tmp_path / "manual.txt"
    source.write_text(
        "Canonical JSON sorts object keys and uses stable separators.\f"
        "Bread dough fermentation depends on temperature.\f",
        encoding="utf-8",
    )
    task_bank = {
        "contract_version": "agiwiki.retrieval-eval-task-bank.v1",
        "task_bank_id": "fragment-smoke",
        "pack_id": "pack_" + "a" * 32,
        "source_digest": "sha256:" + "b" * 64,
        "cases": [
            {
                "case_id": "positive-json",
                "query": "How does canonical JSON sort object keys?",
                "expected_found": True,
                "expected_entry_id": "entry_" + "c" * 32,
            },
            {
                "case_id": "negative-websocket",
                "query": "How are WebSocket client frames masked?",
                "expected_found": False,
                "expected_entry_id": None,
            },
        ],
    }
    task_bank_path = tmp_path / "tasks.json"
    task_bank_path.write_text(json.dumps(task_bank), encoding="utf-8")
    evidence = {
        "contract_version": "agiwiki.fragment-evidence.v1",
        "task_bank_id": "fragment-smoke",
        "source_text_digest": file_sha256(source),
        "page_count": 2,
        "cases": [
            {"case_id": "positive-json", "evidence_pages": [[1, 1]]},
            {"case_id": "negative-websocket", "evidence_pages": []},
        ],
    }
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    report = evaluate_fragments(source, task_bank_path, evidence_path, top_k=2)
    assert report["all_passed"] is True
    assert report["positive_evidence_recall"] == 1
    assert report["negative_no_match_correct"] == 1
    assert report["median_context_characters"] > 0
    assert report["median_positive_context_characters"] > 0

    source.write_text("changed\f", encoding="utf-8")
    with pytest.raises(EvaluationError, match="digest"):
        evaluate_fragments(source, task_bank_path, evidence_path)
