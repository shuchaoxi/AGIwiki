"""Replay a closed retrieval task bank against one exact Memory Pack."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agiwiki.codec import canonical_json, load_json_document, sha256_digest
from agiwiki.index import ensure_index, find_memory
from agiwiki.pack import get_entry, verify_pack

TASK_BANK_CONTRACT = "agiwiki.retrieval-eval-task-bank.v1"
REPORT_CONTRACT = "agiwiki.retrieval-eval-report.v1"
_TASK_BANK_KEYS = {
    "contract_version",
    "task_bank_id",
    "pack_id",
    "source_digest",
    "cases",
}
_CASE_KEYS = {"case_id", "query", "expected_found", "expected_entry_id"}


class EvaluationError(ValueError):
    """The supplied task bank or Pack cannot produce a trustworthy report."""


def evaluate(pack_path: str | Path, task_bank_path: str | Path) -> dict[str, Any]:
    """Return deterministic top-1 and no-match metrics without exposing queries."""

    task_bank = load_task_bank(task_bank_path)
    manifest = verify_pack(pack_path)
    if manifest["pack_id"] != task_bank["pack_id"]:
        raise EvaluationError("task bank is bound to another Pack")

    outcomes: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="agiwiki-retrieval-eval-") as root:
        index_path = Path(root) / "index.sqlite3"
        ensure_index(pack_path, index_path)
        for case in task_bank["cases"]:
            result = find_memory(pack_path, index_path, case["query"], limit=1)
            actual_entry_id = (
                result["results"][0]["entry_id"] if result["results"] else None
            )
            context_characters = 0
            if actual_entry_id is not None:
                context_characters = len(
                    canonical_json(get_entry(pack_path, actual_entry_id, verify=False))
                )
            passed = (
                actual_entry_id == case["expected_entry_id"]
                if case["expected_found"]
                else actual_entry_id is None
            )
            outcomes.append(
                {
                    "case_id": case["case_id"],
                    "expected_found": case["expected_found"],
                    "actual_found": actual_entry_id is not None,
                    "expected_entry_id": case["expected_entry_id"],
                    "actual_entry_id": actual_entry_id,
                    "context_characters": context_characters,
                    "passed": passed,
                }
            )

    positive = [item for item in outcomes if item["expected_found"]]
    negative = [item for item in outcomes if not item["expected_found"]]
    context_sizes = sorted(item["context_characters"] for item in outcomes)
    positive_context_sizes = sorted(item["context_characters"] for item in positive)
    return {
        "contract_version": REPORT_CONTRACT,
        "task_bank_id": task_bank["task_bank_id"],
        "task_bank_digest": sha256_digest(task_bank),
        "pack_id": manifest["pack_id"],
        "manifest_digest": manifest["manifest_digest"],
        "case_count": len(outcomes),
        "positive_top1_correct": sum(item["passed"] for item in positive),
        "positive_count": len(positive),
        "negative_no_match_correct": sum(item["passed"] for item in negative),
        "negative_count": len(negative),
        "median_context_characters": _median(context_sizes),
        "median_positive_context_characters": _median(positive_context_sizes),
        "all_passed": all(item["passed"] for item in outcomes),
        "outcomes": outcomes,
    }


def _median(values: list[int]) -> int | float:
    if not values:
        return 0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def load_task_bank(path: str | Path) -> dict[str, Any]:
    value = load_json_document(path, max_bytes=256 * 1024)
    if set(value) != _TASK_BANK_KEYS:
        raise EvaluationError("task bank fields do not match the closed contract")
    if value["contract_version"] != TASK_BANK_CONTRACT:
        raise EvaluationError("task bank contract version is unsupported")
    if not isinstance(value["task_bank_id"], str) or not value["task_bank_id"].strip():
        raise EvaluationError("task_bank_id must contain text")
    if not isinstance(value["pack_id"], str) or not value["pack_id"].startswith(
        "pack_"
    ):
        raise EvaluationError("task bank pack_id is invalid")
    if not isinstance(value["source_digest"], str) or not value[
        "source_digest"
    ].startswith("sha256:"):
        raise EvaluationError("task bank source_digest is invalid")
    cases = value["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
        raise EvaluationError("task bank must contain 1 to 1000 cases")
    seen: set[str] = set()
    for case in cases:
        _validate_case(case, seen)
    return value


def _validate_case(value: object, seen: set[str]) -> None:
    if not isinstance(value, Mapping) or set(value) != _CASE_KEYS:
        raise EvaluationError("task bank case fields do not match the closed contract")
    case_id = value["case_id"]
    query = value["query"]
    expected_found = value["expected_found"]
    expected_entry_id = value["expected_entry_id"]
    if not isinstance(case_id, str) or not case_id or case_id in seen:
        raise EvaluationError("task bank case_id is invalid or duplicated")
    seen.add(case_id)
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 1000:
        raise EvaluationError("task bank query must contain 1 to 1000 characters")
    if type(expected_found) is not bool:
        raise EvaluationError("expected_found must be boolean")
    if expected_found:
        if not isinstance(expected_entry_id, str) or not expected_entry_id.startswith(
            "entry_"
        ):
            raise EvaluationError("a positive case requires expected_entry_id")
    elif expected_entry_id is not None:
        raise EvaluationError("a negative case must use null expected_entry_id")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay an AGIWiki retrieval task bank against an exact Pack."
    )
    parser.add_argument("pack", help="Path to the exact Memory Pack directory")
    parser.add_argument("task_bank", help="Path to the retrieval task-bank JSON")
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.pack, args.task_bank)
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
