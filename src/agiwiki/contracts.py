"""Closed JSON contracts for an editable AGIWiki memory Workspace.

The contracts in this module deliberately describe portable memory, not a
local source-file binding or an installed Pack.  In particular, a Source may
name a public canonical URI, but it cannot carry a filesystem path or URI
credentials across the Workspace-to-Pack boundary.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from .codec import (
    JSONDocumentError,
    canonical_json,
    load_json_document,
    sha256_digest,
)

WORKSPACE_CONTRACT = "agiwiki.workspace.v1"
SOURCE_CONTRACT = "agiwiki.source.v1"
ENTRY_CONTRACT = "agiwiki.entry.v1"
MAX_JSON_BYTES = 2 * 1024 * 1024

_SCHEMA_FILES = {
    "workspace": "workspace.schema.json",
    "source": "source.schema.json",
    "entry": "entry.schema.json",
    "memory-pack": "memory-pack.schema.json",
    "pack-entry": "pack-entry.schema.json",
    "pack-sources": "pack-sources.schema.json",
}
_SECRET_QUERY_KEYS = frozenset(
    {
        "access_key",
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "auth_token",
        "bearer",
        "client_secret",
        "credential",
        "key",
        "password",
        "private_key",
        "secret",
        "security_token",
        "session_token",
        "sig",
        "signature",
        "token",
        "x_amz_credential",
        "x_amz_security_token",
        "x_amz_signature",
        "x_goog_credential",
        "x_goog_signature",
    }
)
_PRIVATE_POSIX_PATH = re.compile(
    r"(?<![A-Za-z0-9:])/(?:home|Users|root|tmp|private|var/tmp)/[^\s\"']+"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?:(?<![A-Za-z0-9])[A-Z]:[\\/][^\s\"']+|\\\\[^\\\s]+\\[^\\\s]+)"
)


class ContractError(JSONDocumentError):
    """A JSON document is malformed, unsafe, or outside a frozen contract."""


def schema_directory() -> Path:
    return Path(__file__).with_name("schemas")


@lru_cache(maxsize=1)
def schema_validators() -> Mapping[str, Draft202012Validator]:
    """Load and validate the complete frozen Workspace schema bundle."""

    documents: dict[str, dict[str, Any]] = {}
    registry = Registry()
    for short_name, filename in _SCHEMA_FILES.items():
        path = schema_directory() / filename
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(document)
            resource = Resource.from_contents(document)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise ContractError(f"packaged schema is invalid: {filename}") from exc
        documents[short_name] = document
        registry = registry.with_resource(str(document["$id"]), resource)
    checker = FormatChecker()
    return MappingProxyType(
        {
            short_name: Draft202012Validator(
                document,
                registry=registry,
                format_checker=checker,
            )
            for short_name, document in documents.items()
        }
    )


def get_schema_validator(name: str) -> Draft202012Validator:
    normalized = name.removesuffix(".schema.json")
    validator = schema_validators().get(normalized)
    if validator is None:
        raise ContractError(f"unknown AGIWiki schema: {name}")
    return validator


def validate_document(
    name: str,
    value: Mapping[str, Any],
    *,
    source_path: str | os.PathLike[str] | None = None,
) -> None:
    """Validate one closed document and report its first exact JSON Pointer."""

    if not isinstance(value, Mapping):
        raise ContractError(_error_prefix(source_path) + "document must be an object")
    errors = sorted(
        get_schema_validator(name).iter_errors(dict(value)),
        key=lambda item: (
            tuple(str(part) for part in item.absolute_path),
            str(item.validator),
            item.message,
        ),
    )
    if errors:
        error = errors[0]
        pointer = _json_pointer(error.absolute_path)
        raise ContractError(
            f"{_error_prefix(source_path)}{name} invalid at {pointer}: {error.message}"
        )


def normalize_workspace(
    value: Mapping[str, Any],
    *,
    source_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    validate_document("workspace", candidate, source_path=source_path)
    _require_trimmed(candidate["title"], "title", source_path)
    if "description" in candidate:
        _require_trimmed(candidate["description"], "description", source_path)
    _reject_private_paths(candidate, source_path=source_path)
    canonical_json(candidate)
    return candidate


def normalize_source(
    value: Mapping[str, Any],
    *,
    source_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    validate_document("source", candidate, source_path=source_path)
    _require_trimmed(candidate["title"], "title", source_path)
    if candidate["edition"] is not None:
        _require_trimmed(candidate["edition"], "edition", source_path)
    _reject_private_paths(
        {"title": candidate["title"], "edition": candidate["edition"]},
        source_path=source_path,
    )
    _validate_canonical_uri(candidate["canonical_uri"], source_path=source_path)
    canonical_json(candidate)
    return candidate


def normalize_entry(
    value: Mapping[str, Any],
    *,
    source_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    candidate = deepcopy(dict(value))
    validate_document("entry", candidate, source_path=source_path)
    _require_trimmed(candidate["title"], "title", source_path)
    _require_trimmed(candidate["summary"], "summary", source_path)
    candidate["keywords"] = sorted(candidate["keywords"])
    candidate["applies_to"] = sorted(candidate["applies_to"])
    candidate["relations"] = sorted(candidate["relations"], key=canonical_json)
    candidate["source_refs"] = sorted(candidate["source_refs"], key=canonical_json)
    if candidate["kind"] == "fact":
        candidate["content"]["qualifiers"] = sorted(
            candidate["content"]["qualifiers"], key=canonical_json
        )
    _require_unique_structured_ids(candidate, source_path=source_path)
    _reject_private_paths(
        {
            "title": candidate["title"],
            "summary": candidate["summary"],
            "content": candidate["content"],
            "keywords": candidate["keywords"],
            "applies_to": candidate["applies_to"],
        },
        source_path=source_path,
    )
    for reference in candidate["source_refs"]:
        locator = reference["locator"]
        if locator["type"] != "json_pointer":
            _reject_private_paths(locator["value"], source_path=source_path)
    canonical_json(candidate)
    return candidate


def _validate_canonical_uri(
    value: object,
    *,
    source_path: str | os.PathLike[str] | None,
) -> None:
    if value is None:
        return
    if not isinstance(value, str):  # JSON Schema normally catches this first.
        raise ContractError(_error_prefix(source_path) + "invalid canonical_uri")
    if any(ord(character) < 32 for character in value) or value != value.strip():
        raise ContractError(_error_prefix(source_path) + "canonical_uri is unsafe")
    decoded = unquote(value)
    if any(ord(character) < 32 for character in decoded):
        raise ContractError(_error_prefix(source_path) + "canonical_uri is unsafe")
    parsed = urlsplit(decoded)
    if parsed.scheme.lower() not in {"http", "https", "urn", "doi"}:
        raise ContractError(
            _error_prefix(source_path)
            + "canonical_uri must be a portable http(s), urn, or doi URI"
        )
    if parsed.scheme.lower() in {"http", "https"} and not parsed.hostname:
        raise ContractError(_error_prefix(source_path) + "canonical_uri has no host")
    if parsed.username is not None or parsed.password is not None:
        raise ContractError(
            _error_prefix(source_path) + "canonical_uri must not contain credentials"
        )
    parameter_sets = [parse_qsl(parsed.query, keep_blank_values=True)]
    if "=" in parsed.fragment or "&" in parsed.fragment:
        parameter_sets.append(parse_qsl(parsed.fragment, keep_blank_values=True))
    for parameters in parameter_sets:
        if any(_secret_parameter(key) for key, _ in parameters):
            raise ContractError(
                _error_prefix(source_path)
                + "canonical_uri must not contain secret query parameters"
            )
        for _, parameter_value in parameters:
            try:
                _reject_private_paths(
                    unquote(parameter_value),
                    source_path=source_path,
                )
            except ContractError as exc:
                raise ContractError(
                    _error_prefix(source_path)
                    + "canonical_uri must not contain a private path"
                ) from exc


def _secret_parameter(value: str) -> bool:
    normalized = re.sub(r"[-.:]+", "_", value.casefold())
    return normalized in _SECRET_QUERY_KEYS or normalized.endswith(
        ("_access_token", "_api_key", "_credential", "_signature")
    )


def _require_unique_structured_ids(
    entry: Mapping[str, Any],
    *,
    source_path: str | os.PathLike[str] | None,
) -> None:
    content = entry["content"]
    collections: list[tuple[str, list[Mapping[str, Any]], str]] = []
    if entry["kind"] == "procedure":
        collections.append(("content.steps", content["steps"], "step_id"))
    if entry["kind"] == "troubleshooting":
        collections.extend(
            (
                ("content.diagnostic_steps", content["diagnostic_steps"], "step_id"),
                ("content.fixes", content["fixes"], "fix_id"),
            )
        )
    for label, values, id_field in collections:
        identities = [item[id_field] for item in values]
        if len(identities) != len(set(identities)):
            raise ContractError(
                _error_prefix(source_path) + f"{label} contains duplicate {id_field}"
            )


def _reject_private_paths(
    value: Any,
    *,
    source_path: str | os.PathLike[str] | None,
    pointer: str = "",
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            _reject_private_paths(
                item,
                source_path=source_path,
                pointer=f"{pointer}/{escaped}",
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_private_paths(
                item,
                source_path=source_path,
                pointer=f"{pointer}/{index}",
            )
    elif isinstance(value, str) and (
        _PRIVATE_POSIX_PATH.search(value) is not None
        or _WINDOWS_ABSOLUTE_PATH.search(value) is not None
    ):
        raise ContractError(
            _error_prefix(source_path)
            + f"private absolute path is forbidden at {pointer or '/'}"
        )


def _json_pointer(parts: Any) -> str:
    encoded = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "/" + "/".join(encoded) if encoded else "/"


def _error_prefix(path: str | os.PathLike[str] | None) -> str:
    return "" if path is None else f"{Path(path)}: "


def _require_trimmed(
    value: str,
    field_name: str,
    source_path: str | os.PathLike[str] | None,
) -> None:
    if value != value.strip() or "\x00" in value:
        raise ContractError(
            _error_prefix(source_path) + f"{field_name} must be trimmed"
        )


__all__ = [
    "ENTRY_CONTRACT",
    "MAX_JSON_BYTES",
    "SOURCE_CONTRACT",
    "WORKSPACE_CONTRACT",
    "ContractError",
    "canonical_json",
    "get_schema_validator",
    "load_json_document",
    "normalize_entry",
    "normalize_source",
    "normalize_workspace",
    "schema_directory",
    "schema_validators",
    "sha256_digest",
    "validate_document",
]
