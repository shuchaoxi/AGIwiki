"""Replay contexts frozen by an external retrieval system without calling it."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agiwiki.codec import file_sha256, sha256_digest

if __package__:
    from .evaluate_fragment_retrieval import _load_evidence
    from .evaluate_retrieval import EvaluationError, load_task_bank
    from .evaluation_contracts import (
        FrozenEvaluationError,
        load_frozen_retrieval_run,
        load_usage_receipt,
    )
else:
    from evaluate_fragment_retrieval import _load_evidence
    from evaluate_retrieval import EvaluationError, load_task_bank
    from evaluation_contracts import (
        FrozenEvaluationError,
        load_frozen_retrieval_run,
        load_usage_receipt,
    )

REPORT_CONTRACT = "agiwiki.frozen-retrieval-report.v1"
INTERPRETATION = "frozen_external_context_replay_only_not_answer_quality_or_verified_provider_execution"


def evaluate_frozen_retrieval(
    source_text_path: str | Path,
    task_bank_path: str | Path,
    evidence_path: str | Path,
    retrieval_run_path: str | Path,
    *,
    usage_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate and score an already-frozen external context run."""

    task_bank = load_task_bank(task_bank_path)
    evidence = _load_evidence(evidence_path, task_bank)
    source_path = Path(source_text_path)
    source_text_digest = file_sha256(source_path)
    if source_text_digest != evidence["source_text_digest"]:
        raise FrozenEvaluationError(
            "source text digest does not match evidence manifest"
        )
    try:
        source_text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise FrozenEvaluationError("source text must be readable UTF-8") from exc
    pages = source_text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if len(pages) != evidence["page_count"]:
        raise FrozenEvaluationError(
            "source page count does not match evidence manifest"
        )
    retrieval_run = load_frozen_retrieval_run(
        retrieval_run_path,
        task_bank=task_bank,
        source_text_digest=source_text_digest,
        page_count=evidence["page_count"],
    )
    retrieval_run_digest = sha256_digest(retrieval_run)
    usage = (
        load_usage_receipt(usage_path, retrieval_run_digest=retrieval_run_digest)
        if usage_path is not None
        else None
    )

    evidence_by_id = {
        item["case_id"]: item["evidence_pages"] for item in evidence["cases"]
    }
    run_by_id = {item["case_id"]: item for item in retrieval_run["cases"]}
    outcomes: list[dict[str, Any]] = []
    for task in task_bank["cases"]:
        case = run_by_id[task["case_id"]]
        ranges = evidence_by_id[task["case_id"]]
        first_hit_rank = _first_evidence_rank(case["contexts"], ranges)
        context_characters = sum(len(item["text"]) for item in case["contexts"])
        outcomes.append(
            {
                "case_id": task["case_id"],
                "expected_found": task["expected_found"],
                "decision": case["decision"],
                "delivered_context_count": len(case["contexts"]),
                "context_characters": context_characters,
                "evidence_hit": first_hit_rank is not None,
                "first_evidence_rank": first_hit_rank,
            }
        )

    positive = [item for item in outcomes if item["expected_found"]]
    negative = [item for item in outcomes if not item["expected_found"]]
    all_sizes = sorted(item["context_characters"] for item in outcomes)
    positive_sizes = sorted(item["context_characters"] for item in positive)
    reciprocal_rank_sum = sum(
        1 / item["first_evidence_rank"]
        for item in positive
        if item["first_evidence_rank"] is not None
    )
    retriever = retrieval_run["retriever"]
    return {
        "contract_version": REPORT_CONTRACT,
        "task_bank_id": task_bank["task_bank_id"],
        "task_bank_digest": sha256_digest(task_bank),
        "evidence_digest": sha256_digest(evidence),
        "source_digest": task_bank["source_digest"],
        "source_text_digest": source_text_digest,
        "retrieval_run_digest": retrieval_run_digest,
        "retriever_digest": sha256_digest(retriever),
        "declared_retrieval_family": retriever["retrieval_family"],
        "declared_reranker": retriever["reranker_model"] is not None,
        "declared_top_k": retrieval_run["declared_top_k"],
        "case_count": len(outcomes),
        "positive_evidence_recall": sum(item["evidence_hit"] for item in positive),
        "positive_count": len(positive),
        "positive_evidence_mrr": (
            round(reciprocal_rank_sum / len(positive), 6) if positive else 0.0
        ),
        "negative_no_match_correct": sum(
            item["decision"] == "no_match" for item in negative
        ),
        "negative_count": len(negative),
        "error_count": sum(item["decision"] == "error" for item in outcomes),
        "total_context_characters": sum(all_sizes),
        "median_context_characters": _median(all_sizes),
        "median_positive_context_characters": _median(positive_sizes),
        "usage_receipt_digest": sha256_digest(usage) if usage is not None else None,
        "usage": usage,
        "valid_run": True,
        "interpretation": INTERPRETATION,
        "outcomes": outcomes,
    }


def _first_evidence_rank(
    contexts: list[Mapping[str, Any]], evidence_ranges: list[list[int]]
) -> int | None:
    for context in contexts:
        if any(
            context_start <= evidence_end and evidence_start <= context_end
            for context_start, context_end in context["source_page_ranges"]
            for evidence_start, evidence_end in evidence_ranges
        ):
            return context["rank"]
    return None


def _median(values: list[int]) -> int | float:
    if not values:
        return 0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay an externally frozen retrieval-context run."
    )
    parser.add_argument("source_text", help="Exact UTF-8 source-text snapshot")
    parser.add_argument("task_bank", help="Frozen retrieval task-bank JSON")
    parser.add_argument("evidence", help="Evidence-page manifest JSON")
    parser.add_argument("retrieval_run", help="Externally frozen retrieval-run JSON")
    parser.add_argument("--usage", help="Optional aggregate usage-receipt JSON")
    args = parser.parse_args(argv)
    try:
        report = evaluate_frozen_retrieval(
            args.source_text,
            args.task_bank,
            args.evidence,
            args.retrieval_run,
            usage_path=args.usage,
        )
    except (EvaluationError, FrozenEvaluationError, OSError, ValueError) as exc:
        print(f"frozen retrieval evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
