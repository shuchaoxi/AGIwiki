from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from agiwiki.contracts import (
    ContractError,
    canonical_json,
    load_json_document,
    normalize_entry,
    normalize_source,
    normalize_workspace,
    schema_validators,
    sha256_digest,
)
from agiwiki.codec import JSONDocumentError


EXAMPLE = Path(__file__).parents[1] / "examples" / "minimal-memory"


def _read(relative: str) -> dict:
    return json.loads((EXAMPLE / relative).read_text(encoding="utf-8"))


def test_frozen_schemas_are_valid_draft_2020_12_contracts() -> None:
    validators = schema_validators()

    assert set(validators) == {
        "workspace",
        "source",
        "entry",
        "memory-pack",
        "pack-entry",
        "pack-sources",
    }
    for validator in validators.values():
        assert isinstance(validator, Draft202012Validator)
        assert validator.schema["$schema"].endswith("2020-12/schema")


def test_examples_cover_all_four_entry_kinds() -> None:
    workspace = normalize_workspace(_read("agiwiki.json"))
    source = normalize_source(_read("sources/python-json-manual.json"))
    entries = [
        normalize_entry(_read(f"entries/{name}"))
        for name in (
            "fact-ensure-ascii.json",
            "concept-canonical-json.json",
            "procedure-write-json.json",
            "troubleshooting-json-decode.json",
        )
    ]

    assert workspace["contract_version"] == "agiwiki.workspace.v1"
    assert source["contract_version"] == "agiwiki.source.v1"
    assert {item["kind"] for item in entries} == {
        "fact",
        "concept",
        "procedure",
        "troubleshooting",
    }


def test_entry_normalization_is_order_independent_for_set_like_fields() -> None:
    entry = _read("entries/fact-ensure-ascii.json")
    reordered = deepcopy(entry)
    reordered["keywords"].reverse()
    reordered["source_refs"].reverse()
    reordered["content"]["qualifiers"].reverse()

    first = normalize_entry(entry)
    second = normalize_entry(reordered)

    assert first == second
    assert canonical_json(first) == canonical_json(second)
    assert sha256_digest(first) == sha256_digest(second)


def test_closed_contract_rejects_unknown_field_with_json_pointer() -> None:
    entry = _read("entries/fact-ensure-ascii.json")
    entry["local_path"] = "/home/user/private/manual.pdf"

    with pytest.raises(ContractError) as error:
        normalize_entry(entry, source_path="entries/fact.json")

    assert "entries/fact.json" in str(error.value)
    assert "entry invalid at /" in str(error.value)
    assert "Additional properties" in str(error.value)


@pytest.mark.parametrize(
    "uri",
    [
        "/home/user/private/manual.pdf",
        "file:///home/user/private/manual.pdf",
        "https://user:password@example.test/manual",
        "https://example.test/manual?access_token=secret",
        "https%3A%2F%2Fuser%3Apassword%40example.test%2Fmanual",
    ],
)
def test_source_rejects_absolute_local_or_secret_bearing_uri(uri: str) -> None:
    source = _read("sources/python-json-manual.json")
    source["canonical_uri"] = uri

    with pytest.raises(ContractError, match="canonical_uri|source invalid"):
        normalize_source(source)


def test_source_accepts_path_free_public_and_opaque_identifiers() -> None:
    source = _read("sources/python-json-manual.json")
    for uri in ("https://example.test/manual#section", "urn:isbn:9780000000000"):
        candidate = {**source, "canonical_uri": uri}
        assert normalize_source(candidate)["canonical_uri"] == uri


@pytest.mark.parametrize(
    "private_path",
    [
        "/home/alice/private/manual.pdf",
        "/root/private/manual.pdf",
        "/Users/alice/Documents/manual.pdf",
        "C:\\Users\\alice\\manual.pdf",
        "\\\\server\\private\\manual.pdf",
    ],
)
def test_portable_entry_rejects_private_absolute_paths(private_path: str) -> None:
    entry = _read("entries/fact-ensure-ascii.json")
    entry["summary"] = f"Read {private_path} for the answer."

    with pytest.raises(ContractError, match="private absolute path"):
        normalize_entry(entry)


def test_portable_entry_does_not_misclassify_uri_scheme_as_windows_drive() -> None:
    entry = _read("entries/fact-ensure-ascii.json")
    entry["summary"] = (
        "The local stdio resource agiwiki://catalog lists activated memory packs."
    )

    assert normalize_entry(entry)["summary"] == entry["summary"]


def test_procedure_requires_verification_failure_guidance_and_warnings() -> None:
    entry = _read("entries/procedure-write-json.json")
    del entry["content"]["steps"][0]["failure_guidance"]

    with pytest.raises(ContractError) as error:
        normalize_entry(entry)

    assert "/content/steps/0" in str(error.value)
    assert "failure_guidance" in str(error.value)


def test_troubleshooting_requires_branches_fix_verification_and_escalation() -> None:
    entry = _read("entries/troubleshooting-json-decode.json")
    del entry["content"]["fixes"][0]["verification"]

    with pytest.raises(ContractError) as error:
        normalize_entry(entry)

    assert "/content/fixes/0" in str(error.value)
    assert "verification" in str(error.value)


def test_structured_step_identifiers_must_be_unique() -> None:
    entry = _read("entries/procedure-write-json.json")
    entry["content"]["steps"][1]["step_id"] = entry["content"]["steps"][0][
        "step_id"
    ]

    with pytest.raises(ContractError, match="duplicate step_id"):
        normalize_entry(entry)


def test_json_loader_rejects_duplicate_keys_invalid_utf8_and_oversize(
    tmp_path: Path,
) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"a":1,"a":2}', encoding="utf-8")
    with pytest.raises(JSONDocumentError, match="duplicate JSON object key"):
        load_json_document(duplicate)

    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(b'{"title":"\xff"}')
    with pytest.raises(JSONDocumentError, match="UTF-8"):
        load_json_document(invalid)

    large = tmp_path / "large.json"
    large.write_text('{"value":"0123456789"}', encoding="utf-8")
    with pytest.raises(JSONDocumentError, match="bounded regular|size limit"):
        load_json_document(large, max_bytes=8)


def test_json_loader_rejects_symlink_and_parent_traversal(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(JSONDocumentError, match="symlink"):
        load_json_document(link)
    with pytest.raises(JSONDocumentError, match="parent traversal"):
        load_json_document(tmp_path / "child" / ".." / "target.json")
