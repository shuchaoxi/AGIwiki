"""Closed contracts for replaying externally produced retrieval contexts.

These contracts belong to repository research tooling.  They are deliberately
separate from the portable AGIWiki Workspace and Memory Pack contracts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agiwiki.codec import load_json_document, sha256_digest

FROZEN_RETRIEVAL_RUN_CONTRACT = "agiwiki.frozen-retrieval-run.v1"
USAGE_RECEIPT_CONTRACT = "agiwiki.evaluation-usage-receipt.v1"
MAX_RETRIEVAL_RUN_BYTES = 32 * 1024 * 1024
MAX_USAGE_RECEIPT_BYTES = 256 * 1024
MAX_CONTEXT_CHARACTERS = 512 * 1024
MAX_CASE_CONTEXT_CHARACTERS = 2 * 1024 * 1024

_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DECIMAL_AMOUNT = re.compile(r"^(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,12})?$")
_RUN_KEYS = {
    "contract_version",
    "task_bank_id",
    "task_bank_digest",
    "source_digest",
    "source_text_digest",
    "retriever",
    "declared_top_k",
    "cases",
}
_RETRIEVER_KEYS = {
    "system",
    "version",
    "retrieval_family",
    "embedding_model",
    "reranker_model",
    "chunking_id",
    "configuration_digest",
    "corpus_snapshot_digest",
}
_CASE_KEYS = {"case_id", "query_digest", "decision", "contexts"}
_CONTEXT_KEYS = {
    "rank",
    "context_id",
    "text",
    "text_digest",
    "source_page_ranges",
}
_USAGE_KEYS = {
    "contract_version",
    "retrieval_run_digest",
    "scope",
    "measurement_source",
    "request_count",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "latency_ms",
    "cost",
    "provider_receipt_digest",
}
_COST_KEYS = {"currency", "amount_decimal"}
_USAGE_MEASUREMENTS = {
    "request_count",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "latency_ms",
    "cost",
}


class FrozenEvaluationError(ValueError):
    """An imported evaluation artifact is incomplete or inconsistent."""


def load_frozen_retrieval_run(
    path: str | Path,
    *,
    task_bank: Mapping[str, Any],
    source_text_digest: str,
    page_count: int,
) -> dict[str, Any]:
    """Load and cross-check one complete external retrieval run."""

    value = load_json_document(path, max_bytes=MAX_RETRIEVAL_RUN_BYTES)
    if set(value) != _RUN_KEYS:
        raise FrozenEvaluationError(
            "frozen retrieval run fields do not match the closed contract"
        )
    if value["contract_version"] != FROZEN_RETRIEVAL_RUN_CONTRACT:
        raise FrozenEvaluationError("frozen retrieval run version is unsupported")
    if value["task_bank_id"] != task_bank["task_bank_id"]:
        raise FrozenEvaluationError("retrieval run belongs to another task bank")
    if value["task_bank_digest"] != sha256_digest(dict(task_bank)):
        raise FrozenEvaluationError("retrieval run task-bank digest does not match")
    if value["source_digest"] != task_bank["source_digest"]:
        raise FrozenEvaluationError("retrieval run source digest does not match")
    if value["source_text_digest"] != source_text_digest:
        raise FrozenEvaluationError("retrieval run source-text digest does not match")
    _validate_retriever(value["retriever"])
    top_k = value["declared_top_k"]
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise FrozenEvaluationError("declared_top_k must be between 1 and 20")

    cases = value["cases"]
    expected_cases = task_bank["cases"]
    if not isinstance(cases, list) or len(cases) != len(expected_cases):
        raise FrozenEvaluationError("retrieval run must contain every task-bank case")
    expected_by_id = {case["case_id"]: case for case in expected_cases}
    actual_ids: set[str] = set()
    for case in cases:
        _validate_run_case(
            case,
            expected_by_id=expected_by_id,
            actual_ids=actual_ids,
            top_k=top_k,
            page_count=page_count,
        )
    if actual_ids != set(expected_by_id):
        raise FrozenEvaluationError("retrieval run case IDs are incomplete")
    return value


def load_usage_receipt(
    path: str | Path, *, retrieval_run_digest: str
) -> dict[str, Any]:
    """Load a bounded aggregate usage receipt for one frozen retrieval run."""

    value = load_json_document(path, max_bytes=MAX_USAGE_RECEIPT_BYTES)
    if set(value) != _USAGE_KEYS:
        raise FrozenEvaluationError(
            "usage receipt fields do not match the closed contract"
        )
    if value["contract_version"] != USAGE_RECEIPT_CONTRACT:
        raise FrozenEvaluationError("usage receipt version is unsupported")
    if value["retrieval_run_digest"] != retrieval_run_digest:
        raise FrozenEvaluationError("usage receipt belongs to another retrieval run")
    if value["scope"] not in {"retrieval_only", "answering_only", "end_to_end"}:
        raise FrozenEvaluationError("usage receipt scope is unsupported")
    source = value["measurement_source"]
    if source not in {"provider_reported", "client_metered", "unavailable"}:
        raise FrozenEvaluationError("usage measurement_source is unsupported")
    for field in _USAGE_MEASUREMENTS - {"cost"}:
        _validate_nullable_count(value[field], field=field)
    _validate_cost(value["cost"])
    provider_digest = value["provider_receipt_digest"]
    if provider_digest is not None and not _is_digest(provider_digest):
        raise FrozenEvaluationError("provider_receipt_digest is invalid")

    measured_values = [value[field] for field in _USAGE_MEASUREMENTS]
    if source == "unavailable":
        if any(item is not None for item in measured_values):
            raise FrozenEvaluationError(
                "unavailable usage must use null for every measurement"
            )
        if provider_digest is not None:
            raise FrozenEvaluationError(
                "unavailable usage cannot claim a provider receipt"
            )
    else:
        if all(item is None for item in measured_values):
            raise FrozenEvaluationError("measured usage needs at least one measurement")
        if source == "provider_reported" and provider_digest is None:
            raise FrozenEvaluationError(
                "provider-reported usage requires provider_receipt_digest"
            )
    return value


def context_text_digest(text: str) -> str:
    """Digest exact UTF-8 context bytes, without JSON string quoting."""

    return sha256_digest(text.encode("utf-8"))


def _validate_retriever(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != _RETRIEVER_KEYS:
        raise FrozenEvaluationError("retriever fields do not match the closed contract")
    _require_text(value["system"], field="retriever system", maximum=200)
    _nullable_text(value["version"], field="retriever version", maximum=200)
    family = value["retrieval_family"]
    if family not in {"sparse", "dense", "hybrid", "other"}:
        raise FrozenEvaluationError("retrieval_family is unsupported")
    _nullable_text(value["embedding_model"], field="embedding_model", maximum=300)
    _nullable_text(value["reranker_model"], field="reranker_model", maximum=300)
    if family in {"dense", "hybrid"} and value["embedding_model"] is None:
        raise FrozenEvaluationError(
            "dense and hybrid retrieval require an embedding_model identifier"
        )
    _require_text(value["chunking_id"], field="chunking_id", maximum=300)
    for field in ("configuration_digest", "corpus_snapshot_digest"):
        if not _is_digest(value[field]):
            raise FrozenEvaluationError(f"{field} is invalid")


def _validate_run_case(
    value: object,
    *,
    expected_by_id: Mapping[str, Mapping[str, Any]],
    actual_ids: set[str],
    top_k: int,
    page_count: int,
) -> None:
    if not isinstance(value, Mapping) or set(value) != _CASE_KEYS:
        raise FrozenEvaluationError(
            "retrieval run case fields do not match the closed contract"
        )
    case_id = value["case_id"]
    if (
        not isinstance(case_id, str)
        or case_id not in expected_by_id
        or case_id in actual_ids
    ):
        raise FrozenEvaluationError("retrieval run case ID is invalid or duplicated")
    actual_ids.add(case_id)
    expected_query_digest = sha256_digest(expected_by_id[case_id]["query"])
    if value["query_digest"] != expected_query_digest:
        raise FrozenEvaluationError("retrieval run query digest does not match")
    decision = value["decision"]
    if decision not in {"match", "no_match", "error"}:
        raise FrozenEvaluationError("retrieval decision is unsupported")
    contexts = value["contexts"]
    if not isinstance(contexts, list) or len(contexts) > top_k:
        raise FrozenEvaluationError("retrieval contexts exceed declared_top_k")
    if decision == "match" and not contexts:
        raise FrozenEvaluationError("match decision requires delivered contexts")
    if decision != "match" and contexts:
        raise FrozenEvaluationError(
            "no_match and error decisions require empty contexts"
        )

    context_ids: set[str] = set()
    total_characters = 0
    for expected_rank, context in enumerate(contexts, start=1):
        total_characters += _validate_context(
            context,
            expected_rank=expected_rank,
            context_ids=context_ids,
            page_count=page_count,
        )
    if total_characters > MAX_CASE_CONTEXT_CHARACTERS:
        raise FrozenEvaluationError("retrieval case context payload is too large")


def _validate_context(
    value: object,
    *,
    expected_rank: int,
    context_ids: set[str],
    page_count: int,
) -> int:
    if not isinstance(value, Mapping) or set(value) != _CONTEXT_KEYS:
        raise FrozenEvaluationError("context fields do not match the closed contract")
    if value["rank"] != expected_rank or type(value["rank"]) is not int:
        raise FrozenEvaluationError("context ranks must be contiguous and start at one")
    context_id = value["context_id"]
    _require_text(context_id, field="context_id", maximum=300)
    if context_id in context_ids:
        raise FrozenEvaluationError("context_id is duplicated within a case")
    context_ids.add(context_id)
    text = value["text"]
    _require_text(text, field="context text", maximum=MAX_CONTEXT_CHARACTERS)
    if value["text_digest"] != context_text_digest(text):
        raise FrozenEvaluationError("context text digest does not match")
    _validate_page_ranges(value["source_page_ranges"], page_count=page_count)
    return len(text)


def _validate_page_ranges(value: object, *, page_count: int) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise FrozenEvaluationError("source_page_ranges must contain 1 to 32 ranges")
    previous_end = 0
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(type(part) is not int for part in item)
            or not 1 <= item[0] <= item[1] <= page_count
            or item[0] <= previous_end
        ):
            raise FrozenEvaluationError(
                "source page ranges must be ordered, disjoint, and in bounds"
            )
        previous_end = item[1]


def _validate_nullable_count(value: object, *, field: str) -> None:
    if value is not None and (type(value) is not int or not 0 <= value <= 10**15):
        raise FrozenEvaluationError(f"{field} must be null or a bounded integer")


def _validate_cost(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping) or set(value) != _COST_KEYS:
        raise FrozenEvaluationError("cost fields do not match the closed contract")
    if not isinstance(value["currency"], str) or not _CURRENCY.fullmatch(
        value["currency"]
    ):
        raise FrozenEvaluationError("cost currency must be a three-letter code")
    if not isinstance(value["amount_decimal"], str) or not _DECIMAL_AMOUNT.fullmatch(
        value["amount_decimal"]
    ):
        raise FrozenEvaluationError("cost amount_decimal is invalid")


def _require_text(value: object, *, field: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise FrozenEvaluationError(f"{field} must contain bounded text")


def _nullable_text(value: object, *, field: str, maximum: int) -> None:
    if value is not None:
        _require_text(value, field=field, maximum=maximum)


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


__all__ = [
    "FROZEN_RETRIEVAL_RUN_CONTRACT",
    "FrozenEvaluationError",
    "MAX_RETRIEVAL_RUN_BYTES",
    "MAX_USAGE_RECEIPT_BYTES",
    "USAGE_RECEIPT_CONTRACT",
    "context_text_digest",
    "load_frozen_retrieval_run",
    "load_usage_receipt",
]
