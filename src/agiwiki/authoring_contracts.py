"""Closed local-control contracts for resumable Artifact authoring."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .codec import (
    JSONDocumentError,
    canonical_json,
    load_json_document,
    sha256_digest,
    stable_id,
)
from .contracts import ContractError, normalize_source

MAX_AUTHOR_INPUT_BYTES = 256 * 1024

_SCHEMAS = {
    "author-plan-v1": "author-plan.schema.json",
    "author-plan-v2": "author-plan-v2.schema.json",
    "author-claim": "author-claim.schema.json",
    "author-batch-result-v1": "author-batch-result.schema.json",
    "author-batch-result-v2": "author-batch-result-v2.schema.json",
    "author-amendment": "author-amendment.schema.json",
    "author-budget-extension": "author-budget-extension.schema.json",
}


class AuthoringContractError(JSONDocumentError):
    """An authoring control document violates its closed contract."""


@lru_cache(maxsize=1)
def authoring_validators() -> dict[str, Draft202012Validator]:
    root = Path(__file__).with_name("schemas")
    result: dict[str, Draft202012Validator] = {}
    for name, filename in _SCHEMAS.items():
        try:
            document = json.loads((root / filename).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(document)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise AuthoringContractError(
                f"packaged authoring schema is invalid: {filename}"
            ) from exc
        result[name] = Draft202012Validator(document)
    return result


def load_authoring_document(path: str | Path) -> dict[str, Any]:
    return load_json_document(path, max_bytes=MAX_AUTHOR_INPUT_BYTES)


def validate_author_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    version = candidate.get("contract_version")
    if version == "agiwiki.author-plan.v1":
        schema = "author-plan-v1"
    elif version == "agiwiki.author-plan.v2":
        schema = "author-plan-v2"
    else:
        raise AuthoringContractError("author plan contract version is unsupported")
    _validate(schema, candidate)
    source = candidate["source"]
    try:
        normalize_source(
            {
                "contract_version": "agiwiki.source.v1",
                "source_id": source["source_id"],
                "kind": source["kind"],
                "title": source["title"],
                "edition": source["edition"],
                "content_digest": source["content_digest"],
                "canonical_uri": source.get("canonical_uri"),
                "language": source["language"],
            }
        )
    except ContractError as exc:
        raise AuthoringContractError(
            "author plan Source metadata violates the portable Source contract"
        ) from exc
    body = {key: item for key, item in candidate.items() if key != "plan_digest"}
    if candidate["plan_digest"] != sha256_digest(body):
        raise AuthoringContractError("author plan digest does not match its body")
    seed = {
        key: item for key, item in body.items() if key not in {"plan_id", "batches"}
    }
    if candidate["plan_id"] != stable_id("authorplan", seed):
        raise AuthoringContractError("author plan ID does not match its immutable seed")
    batches = candidate["batches"]
    if [item["ordinal"] for item in batches] != list(range(1, len(batches) + 1)):
        raise AuthoringContractError("author batch ordinals must be continuous")
    batch_ids = [item["batch_id"] for item in batches]
    if len(batch_ids) != len(set(batch_ids)):
        raise AuthoringContractError("author batch IDs must be unique")
    expected_locator = {
        "page": "page",
        "line": "line_range",
        "file": "file",
    }[candidate["source"]["unit_type"]]
    previous_end = 0
    batch_size = candidate["policy"]["batch_size"]
    tokens_per_unit = candidate["policy"]["tokens_per_unit"]
    for index, item in enumerate(batches):
        locator = item["locator"]
        if locator["type"] != expected_locator:
            raise AuthoringContractError("author batch locator type is inconsistent")
        if locator["start"] != previous_end + 1 or locator["end"] < locator["start"]:
            raise AuthoringContractError("author batch ranges must be contiguous")
        unit_span = locator["end"] - locator["start"] + 1
        if unit_span > batch_size or (
            index < len(batches) - 1 and unit_span != batch_size
        ):
            raise AuthoringContractError(
                "author batch size is inconsistent with policy"
            )
        if item["estimated_input_tokens"] != unit_span * tokens_per_unit:
            raise AuthoringContractError("author batch Token estimate is inconsistent")
        expected_batch_id = stable_id(
            "authorbatch",
            {
                "plan_id": candidate["plan_id"],
                "ordinal": item["ordinal"],
                "locator": locator,
            },
        )
        if item["batch_id"] != expected_batch_id:
            raise AuthoringContractError("author batch ID does not match its range")
        previous_end = locator["end"]
    if previous_end != candidate["source"]["unit_count"]:
        raise AuthoringContractError("author batches must cover every source unit")
    canonical_json(candidate)
    return candidate


def validate_author_claim(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = _validated("author-claim", value)
    body = {key: item for key, item in candidate.items() if key != "claim_digest"}
    if candidate["claim_digest"] != sha256_digest(body):
        raise AuthoringContractError("author claim digest does not match its body")
    if candidate["claim_id"] != stable_id(
        "authorclaim",
        {"plan_id": candidate["plan_id"], "batch_id": candidate["batch_id"]},
    ):
        raise AuthoringContractError("author claim ID does not match its batch")
    return candidate


def validate_author_batch_result(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    version = candidate.get("contract_version")
    if version == "agiwiki.author-batch-result.v1":
        schema = "author-batch-result-v1"
    elif version == "agiwiki.author-batch-result.v2":
        schema = "author-batch-result-v2"
    else:
        raise AuthoringContractError("author batch result version is unsupported")
    candidate = _validated(schema, candidate)
    if "result_digest" not in candidate:
        raise AuthoringContractError("stored author batch result requires a digest")
    body = {key: item for key, item in candidate.items() if key != "result_digest"}
    if candidate["result_digest"] != sha256_digest(body):
        raise AuthoringContractError(
            "author batch result digest does not match its body"
        )
    if version == "agiwiki.author-batch-result.v2":
        bindings = candidate["entry_bindings"]
        entry_ids = [item["entry_id"] for item in bindings]
        if entry_ids != sorted(entry_ids) or len(entry_ids) != len(set(entry_ids)):
            raise AuthoringContractError(
                "author batch result Entry bindings must be unique and sorted"
            )
    return candidate


def normalize_author_batch_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a legacy v1 record request.

    New stored results are sealed as v2 by the controller.  This helper remains
    for validating the stable v1 input/replay shape and reading old callers.
    """

    candidate = deepcopy(dict(value))
    if candidate.get("contract_version") != "agiwiki.author-batch-result.v1":
        raise AuthoringContractError(
            "author record input must use the v1 request shape"
        )
    if "result_digest" not in candidate:
        candidate["result_digest"] = sha256_digest(candidate)
    return validate_author_batch_result(candidate)


def seal_author_batch_result(
    request: Mapping[str, Any],
    entry_bindings: list[Mapping[str, str]],
) -> dict[str, Any]:
    """Seal a validated v1 request as one digest-bound stored v2 result."""

    legacy = normalize_author_batch_result(request)
    bindings = sorted(
        (deepcopy(dict(item)) for item in entry_bindings),
        key=lambda item: item["entry_id"],
    )
    if [item["entry_id"] for item in bindings] != sorted(legacy["entry_ids"]):
        raise AuthoringContractError(
            "sealed Entry bindings do not match the record request"
        )
    body = {
        "contract_version": "agiwiki.author-batch-result.v2",
        "plan_id": legacy["plan_id"],
        "batch_id": legacy["batch_id"],
        "outcome": legacy["outcome"],
        "measurement_source": legacy["measurement_source"],
        "input_tokens": legacy["input_tokens"],
        "output_tokens": legacy["output_tokens"],
        "entry_bindings": bindings,
    }
    return validate_author_batch_result({**body, "result_digest": sha256_digest(body)})


def validate_author_amendment(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = _validated("author-amendment", value)
    body = {key: item for key, item in candidate.items() if key != "amendment_digest"}
    if candidate["amendment_digest"] != sha256_digest(body):
        raise AuthoringContractError("author amendment digest does not match its body")
    if candidate["amendment_id"] != stable_id(
        "authoramend",
        {"plan_id": candidate["plan_id"], "operation_id": candidate["operation_id"]},
    ):
        raise AuthoringContractError("author amendment ID does not match its operation")
    if candidate["old_entry_digest"] == candidate["new_entry_digest"]:
        raise AuthoringContractError("author amendment must change Entry content")
    return candidate


def validate_author_budget_extension(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = _validated("author-budget-extension", value)
    body = {key: item for key, item in candidate.items() if key != "extension_digest"}
    if candidate["extension_digest"] != sha256_digest(body):
        raise AuthoringContractError("author budget digest does not match its body")
    if candidate["extension_id"] != stable_id(
        "authorext",
        {"plan_id": candidate["plan_id"], "operation_id": candidate["operation_id"]},
    ):
        raise AuthoringContractError("author budget ID does not match its operation")
    return candidate


def _validated(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    _validate(name, candidate)
    canonical_json(candidate)
    return candidate


def _validate(name: str, value: Mapping[str, Any]) -> None:
    errors = sorted(
        authoring_validators()[name].iter_errors(dict(value)),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            str(error.validator),
            error.message,
        ),
    )
    if not errors:
        return
    error = errors[0]
    pointer = "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1") for part in error.absolute_path
    )
    raise AuthoringContractError(f"{name} invalid at {pointer or '/'}: {error.message}")


__all__ = [
    "MAX_AUTHOR_INPUT_BYTES",
    "AuthoringContractError",
    "authoring_validators",
    "load_authoring_document",
    "normalize_author_batch_result",
    "seal_author_batch_result",
    "validate_author_amendment",
    "validate_author_batch_result",
    "validate_author_budget_extension",
    "validate_author_claim",
    "validate_author_plan",
]
