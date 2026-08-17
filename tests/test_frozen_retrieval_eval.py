from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from agiwiki.codec import file_sha256, sha256_digest
from tools import evaluation_contracts
from tools.evaluate_frozen_retrieval import evaluate_frozen_retrieval, main
from tools.evaluation_contracts import (
    FrozenEvaluationError,
    context_text_digest,
    load_frozen_retrieval_run,
    load_usage_receipt,
)
from tools.evaluate_retrieval import load_task_bank

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
RAW_QUERY = "RAW_QUERY_DO_NOT_LEAK canonical JSON"
RAW_CONTEXT = "RAW_CONTEXT_DO_NOT_LEAK canonical JSON sorts object keys."


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _artifacts(tmp_path: Path) -> dict[str, object]:
    source = tmp_path / "source.txt"
    source.write_text("canonical JSON evidence\fother topic\f", encoding="utf-8")
    task_bank = {
        "contract_version": "agiwiki.retrieval-eval-task-bank.v1",
        "task_bank_id": "external-smoke",
        "pack_id": "pack_" + "d" * 32,
        "source_digest": DIGEST_A,
        "cases": [
            {
                "case_id": "positive-json",
                "query": RAW_QUERY,
                "expected_found": True,
                "expected_entry_id": "entry_" + "e" * 32,
            },
            {
                "case_id": "negative-websocket",
                "query": "RAW_NEGATIVE_QUERY_DO_NOT_LEAK WebSocket masking",
                "expected_found": False,
                "expected_entry_id": None,
            },
        ],
    }
    task_path = _write(tmp_path / "tasks.json", task_bank)
    evidence = {
        "contract_version": "agiwiki.fragment-evidence.v1",
        "task_bank_id": "external-smoke",
        "source_text_digest": file_sha256(source),
        "page_count": 2,
        "cases": [
            {"case_id": "positive-json", "evidence_pages": [[1, 1]]},
            {"case_id": "negative-websocket", "evidence_pages": []},
        ],
    }
    evidence_path = _write(tmp_path / "evidence.json", evidence)
    run = {
        "contract_version": "agiwiki.frozen-retrieval-run.v1",
        "task_bank_id": "external-smoke",
        "task_bank_digest": sha256_digest(task_bank),
        "source_digest": DIGEST_A,
        "source_text_digest": file_sha256(source),
        "retriever": {
            "system": "example-external-rag",
            "version": "1.2.3",
            "retrieval_family": "hybrid",
            "embedding_model": "example-embedding-v1",
            "reranker_model": "example-reranker-v1",
            "chunking_id": "fixed-400-token-overlap-40-v1",
            "configuration_digest": DIGEST_B,
            "corpus_snapshot_digest": DIGEST_C,
        },
        "declared_top_k": 2,
        "cases": [
            {
                "case_id": "positive-json",
                "query_digest": sha256_digest(RAW_QUERY),
                "decision": "match",
                "contexts": [
                    {
                        "rank": 1,
                        "context_id": "chunk-page-1",
                        "text": RAW_CONTEXT,
                        "text_digest": context_text_digest(RAW_CONTEXT),
                        "source_page_ranges": [[1, 1]],
                    }
                ],
            },
            {
                "case_id": "negative-websocket",
                "query_digest": sha256_digest(
                    "RAW_NEGATIVE_QUERY_DO_NOT_LEAK WebSocket masking"
                ),
                "decision": "no_match",
                "contexts": [],
            },
        ],
    }
    run_path = _write(tmp_path / "run.json", run)
    usage = {
        "contract_version": "agiwiki.evaluation-usage-receipt.v1",
        "retrieval_run_digest": sha256_digest(run),
        "scope": "retrieval_only",
        "measurement_source": "provider_reported",
        "request_count": 2,
        "input_tokens": 17,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "latency_ms": 25,
        "cost": {"currency": "USD", "amount_decimal": "0.0012"},
        "provider_receipt_digest": "sha256:" + "f" * 64,
    }
    usage_path = _write(tmp_path / "usage.json", usage)
    return {
        "source": source,
        "task_bank": task_bank,
        "task_path": task_path,
        "evidence_path": evidence_path,
        "run": run,
        "run_path": run_path,
        "usage": usage,
        "usage_path": usage_path,
    }


def test_frozen_retrieval_replays_without_exposing_query_or_context(
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)
    report = evaluate_frozen_retrieval(
        artifacts["source"],
        artifacts["task_path"],
        artifacts["evidence_path"],
        artifacts["run_path"],
        usage_path=artifacts["usage_path"],
    )

    assert report["valid_run"] is True
    assert report["positive_evidence_recall"] == report["positive_count"] == 1
    assert report["positive_evidence_mrr"] == 1.0
    assert report["negative_no_match_correct"] == report["negative_count"] == 1
    assert report["error_count"] == 0
    assert report["median_positive_context_characters"] == len(RAW_CONTEXT)
    assert report["usage"]["measurement_source"] == "provider_reported"
    serialized = json.dumps(report)
    assert RAW_QUERY not in serialized
    assert RAW_CONTEXT not in serialized
    assert "RAW_NEGATIVE_QUERY_DO_NOT_LEAK" not in serialized


def test_low_recall_is_a_valid_report_and_cli_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts = _artifacts(tmp_path)
    run = deepcopy(artifacts["run"])
    context = run["cases"][0]["contexts"][0]
    context["source_page_ranges"] = [[2, 2]]
    _write(artifacts["run_path"], run)

    exit_code = main(
        [
            str(artifacts["source"]),
            str(artifacts["task_path"]),
            str(artifacts["evidence_path"]),
            str(artifacts["run_path"]),
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["positive_evidence_recall"] == 0
    assert report["valid_run"] is True


def test_source_snapshot_page_count_is_verified(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    evidence = json.loads(Path(artifacts["evidence_path"]).read_text(encoding="utf-8"))
    evidence["page_count"] = 3
    evidence["cases"][0]["evidence_pages"] = [[1, 1]]
    _write(artifacts["evidence_path"], evidence)

    with pytest.raises(FrozenEvaluationError, match="page count"):
        evaluate_frozen_retrieval(
            artifacts["source"],
            artifacts["task_path"],
            artifacts["evidence_path"],
            artifacts["run_path"],
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda run: run.update({"unknown": True}), "closed contract"),
        (lambda run: run.update({"task_bank_digest": DIGEST_C}), "task-bank digest"),
        (lambda run: run.update({"source_digest": DIGEST_C}), "source digest"),
        (
            lambda run: run.update({"source_text_digest": DIGEST_C}),
            "source-text digest",
        ),
        (lambda run: run["cases"].pop(), "every task-bank case"),
        (
            lambda run: run["cases"][1].update({"case_id": "positive-json"}),
            "duplicated",
        ),
        (
            lambda run: run["cases"][0].update({"query_digest": DIGEST_C}),
            "query digest",
        ),
        (
            lambda run: run["cases"][0]["contexts"][0].update(
                {"text_digest": DIGEST_C}
            ),
            "text digest",
        ),
        (
            lambda run: run["cases"][0]["contexts"][0].update({"rank": 2}),
            "ranks",
        ),
        (
            lambda run: run["cases"][0]["contexts"][0].update(
                {"source_page_ranges": [[3, 3]]}
            ),
            "page ranges",
        ),
        (
            lambda run: run["cases"][1].update(
                {
                    "contexts": deepcopy(run["cases"][0]["contexts"]),
                }
            ),
            "require empty contexts",
        ),
        (
            lambda run: run.update({"declared_top_k": 0}),
            "declared_top_k",
        ),
    ],
)
def test_frozen_run_contract_fails_closed(
    tmp_path: Path, mutation: object, message: str
) -> None:
    artifacts = _artifacts(tmp_path)
    run = deepcopy(artifacts["run"])
    mutation(run)
    _write(artifacts["run_path"], run)
    task_bank = load_task_bank(artifacts["task_path"])

    with pytest.raises(FrozenEvaluationError, match=message):
        load_frozen_retrieval_run(
            artifacts["run_path"],
            task_bank=task_bank,
            source_text_digest=file_sha256(artifacts["source"]),
            page_count=2,
        )


def test_frozen_run_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    raw = json.dumps(artifacts["run"])
    raw = raw.replace(
        '{"contract_version":',
        '{"contract_version":"duplicate","contract_version":',
        1,
    )
    artifacts["run_path"].write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        load_frozen_retrieval_run(
            artifacts["run_path"],
            task_bank=load_task_bank(artifacts["task_path"]),
            source_text_digest=file_sha256(artifacts["source"]),
            page_count=2,
        )


def test_frozen_run_enforces_input_size_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = _artifacts(tmp_path)
    monkeypatch.setattr(evaluation_contracts, "MAX_RETRIEVAL_RUN_BYTES", 10)

    with pytest.raises(ValueError, match="size limit|bounded regular file"):
        load_frozen_retrieval_run(
            artifacts["run_path"],
            task_bank=load_task_bank(artifacts["task_path"]),
            source_text_digest=file_sha256(artifacts["source"]),
            page_count=2,
        )


def test_usage_receipt_fails_closed_on_false_unavailable_and_wrong_binding(
    tmp_path: Path,
) -> None:
    artifacts = _artifacts(tmp_path)
    usage = deepcopy(artifacts["usage"])
    usage.update(
        {
            "measurement_source": "unavailable",
            "provider_receipt_digest": None,
            "request_count": 0,
            "input_tokens": None,
            "output_tokens": None,
            "cached_input_tokens": None,
            "latency_ms": None,
            "cost": None,
        }
    )
    _write(artifacts["usage_path"], usage)
    with pytest.raises(FrozenEvaluationError, match="must use null"):
        load_usage_receipt(
            artifacts["usage_path"],
            retrieval_run_digest=sha256_digest(artifacts["run"]),
        )

    usage["request_count"] = None
    usage["retrieval_run_digest"] = DIGEST_C
    _write(artifacts["usage_path"], usage)
    with pytest.raises(FrozenEvaluationError, match="another retrieval run"):
        load_usage_receipt(
            artifacts["usage_path"],
            retrieval_run_digest=sha256_digest(artifacts["run"]),
        )


def test_unavailable_usage_is_explicit_and_uses_null(tmp_path: Path) -> None:
    artifacts = _artifacts(tmp_path)
    usage = deepcopy(artifacts["usage"])
    usage.update(
        {
            "measurement_source": "unavailable",
            "provider_receipt_digest": None,
            "request_count": None,
            "input_tokens": None,
            "output_tokens": None,
            "cached_input_tokens": None,
            "latency_ms": None,
            "cost": None,
        }
    )
    _write(artifacts["usage_path"], usage)

    loaded = load_usage_receipt(
        artifacts["usage_path"],
        retrieval_run_digest=sha256_digest(artifacts["run"]),
    )
    assert loaded["measurement_source"] == "unavailable"
    assert loaded["request_count"] is None
