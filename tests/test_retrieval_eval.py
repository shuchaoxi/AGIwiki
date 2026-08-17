from __future__ import annotations

import json
from pathlib import Path

import pytest

from agiwiki.pack import build_workspace_pack
from agiwiki.workspace import validate_workspace
from tools.evaluate_retrieval import EvaluationError, evaluate


def _task_bank(pack_id: str) -> dict:
    return {
        "contract_version": "agiwiki.retrieval-eval-task-bank.v1",
        "task_bank_id": "minimal-memory-smoke",
        "pack_id": pack_id,
        "source_digest": "sha256:" + "a" * 64,
        "cases": [
            {
                "case_id": "positive-canonical-json",
                "query": "canonical JSON",
                "expected_found": True,
                "expected_entry_id": "entry_44444444444444444444444444444444",
            },
            {
                "case_id": "negative-unrelated-topic",
                "query": "WebSocket frame masking algorithm",
                "expected_found": False,
                "expected_entry_id": None,
            },
        ],
    }


def test_retrieval_task_bank_replays_without_returning_raw_queries(
    tmp_path: Path,
) -> None:
    workspace = validate_workspace(Path("examples/minimal-memory"))
    pack_path = tmp_path / "minimal.memory-pack"
    receipt = build_workspace_pack(workspace, pack_path)
    task_bank_path = tmp_path / "task-bank.json"
    task_bank_path.write_text(
        json.dumps(_task_bank(receipt["pack_id"])), encoding="utf-8"
    )

    report = evaluate(pack_path, task_bank_path)
    assert report["all_passed"] is True
    assert report["positive_top1_correct"] == report["positive_count"] == 1
    assert report["negative_no_match_correct"] == report["negative_count"] == 1
    assert report["median_positive_context_characters"] > 0
    assert "query" not in json.dumps(report)

    mismatched = _task_bank("pack_" + "f" * 32)
    task_bank_path.write_text(json.dumps(mismatched), encoding="utf-8")
    with pytest.raises(EvaluationError, match="another Pack"):
        evaluate(pack_path, task_bank_path)
