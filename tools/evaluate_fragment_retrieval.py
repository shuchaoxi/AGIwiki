"""Evaluate a deterministic page-fragment baseline without calling a model."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agiwiki.codec import file_sha256, load_json_document, sha256_digest

if __package__:
    from .evaluate_retrieval import EvaluationError, load_task_bank
else:
    from evaluate_retrieval import EvaluationError, load_task_bank

EVIDENCE_CONTRACT = "agiwiki.fragment-evidence.v1"
REPORT_CONTRACT = "agiwiki.fragment-retrieval-report.v1"
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9./_-]*")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "can",
    "do",
    "does",
    "how",
    "i",
    "in",
    "instead",
    "is",
    "of",
    "or",
    "should",
    "the",
    "to",
    "what",
    "when",
    "why",
    "with",
    "work",
}
_EVIDENCE_KEYS = {
    "contract_version",
    "task_bank_id",
    "source_text_digest",
    "page_count",
    "cases",
}
_EVIDENCE_CASE_KEYS = {"case_id", "evidence_pages"}


def evaluate_fragments(
    source_text_path: str | Path,
    task_bank_path: str | Path,
    evidence_path: str | Path,
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    """Measure lexical page recall and context size for one frozen task bank."""

    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise EvaluationError("top_k must be between 1 and 20")
    task_bank = load_task_bank(task_bank_path)
    evidence = _load_evidence(evidence_path, task_bank)
    source_path = Path(source_text_path)
    if file_sha256(source_path) != evidence["source_text_digest"]:
        raise EvaluationError("source text digest does not match evidence manifest")
    try:
        source_text = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EvaluationError("source text must be readable UTF-8") from exc
    pages = source_text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if len(pages) != evidence["page_count"]:
        raise EvaluationError("source page count does not match evidence manifest")

    evidence_by_case = {
        item["case_id"]: item["evidence_pages"] for item in evidence["cases"]
    }
    outcomes: list[dict[str, Any]] = []
    for case in task_bank["cases"]:
        terms = _query_terms(case["query"])
        ranked = _rank_pages(pages, terms, top_k=top_k)
        page_numbers = [item[0] for item in ranked]
        expected_ranges = evidence_by_case[case["case_id"]]
        evidence_hit = any(
            start <= page <= end
            for page in page_numbers
            for start, end in expected_ranges
        )
        passed = evidence_hit if case["expected_found"] else not page_numbers
        outcomes.append(
            {
                "case_id": case["case_id"],
                "expected_found": case["expected_found"],
                "retrieved_pages": page_numbers,
                "evidence_hit": evidence_hit,
                "context_characters": sum(len(item[1]) for item in ranked),
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
        "evidence_digest": sha256_digest(evidence),
        "source_text_digest": evidence["source_text_digest"],
        "page_count": len(pages),
        "top_k": top_k,
        "positive_evidence_recall": sum(item["passed"] for item in positive),
        "positive_count": len(positive),
        "negative_no_match_correct": sum(item["passed"] for item in negative),
        "negative_count": len(negative),
        "median_context_characters": _median(context_sizes),
        "median_positive_context_characters": _median(positive_context_sizes),
        "all_passed": all(item["passed"] for item in outcomes),
        "interpretation": "lexical_page_retrieval_only_not_answer_quality",
        "outcomes": outcomes,
    }


def _load_evidence(path: str | Path, task_bank: Mapping[str, Any]) -> dict[str, Any]:
    value = load_json_document(path, max_bytes=256 * 1024)
    if set(value) != _EVIDENCE_KEYS:
        raise EvaluationError(
            "fragment evidence fields do not match the closed contract"
        )
    if value["contract_version"] != EVIDENCE_CONTRACT:
        raise EvaluationError("fragment evidence contract version is unsupported")
    if value["task_bank_id"] != task_bank["task_bank_id"]:
        raise EvaluationError("fragment evidence belongs to another task bank")
    if not isinstance(value["source_text_digest"], str) or not value[
        "source_text_digest"
    ].startswith("sha256:"):
        raise EvaluationError("fragment evidence source digest is invalid")
    if type(value["page_count"]) is not int or not 1 <= value["page_count"] <= 100_000:
        raise EvaluationError("fragment evidence page count is invalid")
    cases = value["cases"]
    if not isinstance(cases, list) or len(cases) != len(task_bank["cases"]):
        raise EvaluationError("fragment evidence case count is inconsistent")
    expected_ids = {item["case_id"] for item in task_bank["cases"]}
    actual_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping) or set(case) != _EVIDENCE_CASE_KEYS:
            raise EvaluationError("fragment evidence case fields are invalid")
        case_id = case["case_id"]
        if (
            not isinstance(case_id, str)
            or case_id in actual_ids
            or case_id not in expected_ids
        ):
            raise EvaluationError("fragment evidence case ID is invalid or duplicated")
        actual_ids.add(case_id)
        _validate_ranges(case["evidence_pages"], page_count=value["page_count"])
        expected_found = next(
            item["expected_found"]
            for item in task_bank["cases"]
            if item["case_id"] == case_id
        )
        if expected_found != bool(case["evidence_pages"]):
            raise EvaluationError("fragment evidence answerability is inconsistent")
    if actual_ids != expected_ids:
        raise EvaluationError("fragment evidence case IDs are inconsistent")
    return value


def _validate_ranges(value: object, *, page_count: int) -> None:
    if not isinstance(value, list) or len(value) > 32:
        raise EvaluationError("evidence_pages must be a bounded array")
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(type(part) is not int for part in item)
            or not 1 <= item[0] <= item[1] <= page_count
        ):
            raise EvaluationError("evidence page range is invalid")


def _query_terms(query: str) -> tuple[str, ...]:
    terms = [
        match.group(0).casefold()
        for match in _WORD.finditer(query)
        if len(match.group(0)) >= 2 and match.group(0).casefold() not in _STOPWORDS
    ]
    return tuple(dict.fromkeys(terms))[:32]


def _rank_pages(
    pages: list[str], terms: tuple[str, ...], *, top_k: int
) -> list[tuple[int, str]]:
    if not terms:
        return []
    minimum_matches = 1 if len(terms) == 1 else math.ceil(len(terms) * 0.6)
    ranked: list[tuple[int, int, int, str]] = []
    for page_number, page in enumerate(pages, start=1):
        folded = page.casefold()
        matches = sum(term in folded for term in terms)
        if matches < minimum_matches:
            continue
        frequency = sum(folded.count(term) for term in terms)
        ranked.append((-matches, -frequency, page_number, page))
    ranked.sort()
    return [(item[2], item[3]) for item in ranked[:top_k]]


def _median(values: list[int]) -> int | float:
    if not values:
        return 0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate a deterministic lexical page-fragment baseline."
    )
    parser.add_argument("source_text", help="UTF-8 text with form-feed page separators")
    parser.add_argument("task_bank", help="Retrieval task-bank JSON")
    parser.add_argument("evidence", help="Private or public evidence-page manifest")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        report = evaluate_fragments(
            args.source_text, args.task_bank, args.evidence, top_k=args.top_k
        )
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"fragment evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
