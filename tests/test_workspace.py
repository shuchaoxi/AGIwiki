from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import pytest

from agiwiki.workspace import (
    WorkspaceError,
    initialize_workspace,
    load_workspace,
    validate_workspace,
    workspace_digest,
)


EXAMPLE = Path(__file__).parents[1] / "examples" / "minimal-memory"
SOURCE_ID = "src_22222222222222222222222222222222"
FACT_ID = "entry_33333333333333333333333333333333"
CONCEPT_ID = "entry_44444444444444444444444444444444"


def _copy_example(tmp_path: Path) -> Path:
    target = tmp_path / "memory"
    shutil.copytree(EXAMPLE, target)
    return target


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_initialize_workspace_creates_private_empty_authoring_tree(
    tmp_path: Path,
) -> None:
    root = tmp_path / "research-memory"

    manifest = initialize_workspace(
        root,
        slug="research-memory",
        title="研究事实记忆",
    )

    assert manifest["workspace_id"].startswith("ws_")
    assert json.loads((root / "agiwiki.json").read_text(encoding="utf-8")) == manifest
    assert (root / "sources").is_dir()
    assert (root / "entries").is_dir()
    assert os.stat(root).st_mode & 0o077 == 0
    assert os.stat(root / "agiwiki.json").st_mode & 0o077 == 0
    with pytest.raises(WorkspaceError, match="at least one Source"):
        validate_workspace(root)


def test_initialize_workspace_is_no_clobber_and_rejects_symlink_parent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "memory"
    root.mkdir()
    sentinel = root / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="already exists"):
        initialize_workspace(root, slug="memory", title="Memory")
    assert sentinel.read_text(encoding="utf-8") == "keep"

    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(WorkspaceError, match="symlink"):
        initialize_workspace(linked / "child", slug="child", title="Child")

    invalid = tmp_path / "invalid"
    with pytest.raises(WorkspaceError, match="workspace invalid"):
        initialize_workspace(invalid, slug="Not A Slug", title="Invalid")
    assert not invalid.exists()


def test_minimal_workspace_loads_and_locates_editable_entry() -> None:
    workspace = load_workspace(EXAMPLE)

    assert workspace.workspace_id == "ws_11111111111111111111111111111111"
    assert len(workspace.sources) == 1
    assert len(workspace.entries) == 4
    assert workspace.source(SOURCE_ID)["title"] == "Python json 模块文档"
    assert workspace.entry(FACT_ID)["kind"] == "fact"
    assert workspace.locate_entry(FACT_ID).name == "fact-ensure-ascii.json"
    assert workspace.workspace_digest.startswith("sha256:")


def test_validate_rejects_structurally_valid_but_information_poor_entry(
    tmp_path: Path,
) -> None:
    root = _copy_example(tmp_path)
    fact_path = root / "entries" / "fact-ensure-ascii.json"
    fact = _load(fact_path)
    fact["summary"] = "." * 64
    fact["content"]["statement"] = "." * 64
    fact["keywords"] = ["x", "y"]
    _write(fact_path, fact)

    assert load_workspace(root).entry(FACT_ID)["summary"] == "." * 64
    with pytest.raises(WorkspaceError, match="too brief at /summary"):
        validate_workspace(root)


def test_validate_is_read_only_and_portable_projection_contains_no_local_paths(
    tmp_path: Path,
) -> None:
    root = _copy_example(tmp_path)
    before = {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
    }

    workspace = validate_workspace(root)

    after = {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
    }
    assert before == after
    portable = json.dumps(workspace.to_portable_dict(), ensure_ascii=False)
    assert str(root) not in portable
    assert "/home/" not in portable
    assert "password" not in portable.lower()


def test_digest_is_independent_of_filenames_key_order_and_set_order(
    tmp_path: Path,
) -> None:
    root = _copy_example(tmp_path)
    first = load_workspace(root)
    fact_path = root / "entries" / "fact-ensure-ascii.json"
    fact = _load(fact_path)
    fact["keywords"].reverse()
    fact_path.unlink()
    _write(root / "entries" / "renamed.json", dict(reversed(list(fact.items()))))

    second = load_workspace(root)

    assert first.workspace_digest == second.workspace_digest
    assert workspace_digest(second.manifest, second.sources, second.entries) == (
        second.workspace_digest
    )


def test_duplicate_source_and_entry_identities_are_rejected(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    source = _load(root / "sources" / "python-json-manual.json")
    _write(root / "sources" / "duplicate.json", source)

    with pytest.raises(WorkspaceError, match="duplicate Source identity"):
        load_workspace(root)

    (root / "sources" / "duplicate.json").unlink()
    entry = _load(root / "entries" / "fact-ensure-ascii.json")
    _write(root / "entries" / "duplicate.json", entry)
    with pytest.raises(WorkspaceError, match="duplicate Entry identity"):
        load_workspace(root)


def test_every_source_ref_and_relation_must_resolve_inside_workspace(
    tmp_path: Path,
) -> None:
    root = _copy_example(tmp_path)
    fact_path = root / "entries" / "fact-ensure-ascii.json"
    fact = _load(fact_path)
    fact["source_refs"][0]["source_id"] = "src_99999999999999999999999999999999"
    _write(fact_path, fact)

    with pytest.raises(WorkspaceError, match="source_ref.*not in this Workspace"):
        load_workspace(root)

    fact["source_refs"][0]["source_id"] = SOURCE_ID
    fact["relations"][0]["target_entry_id"] = (
        "entry_99999999999999999999999999999999"
    )
    _write(fact_path, fact)
    with pytest.raises(WorkspaceError, match="relation target.*not in this Workspace"):
        load_workspace(root)


def test_self_relation_is_rejected(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    fact_path = root / "entries" / "fact-ensure-ascii.json"
    fact = _load(fact_path)
    fact["relations"][0]["target_entry_id"] = FACT_ID
    _write(fact_path, fact)

    with pytest.raises(WorkspaceError, match="cannot target itself"):
        load_workspace(root)


@pytest.mark.parametrize("target", ["root", "sources", "entry"])
def test_workspace_rejects_symlink_roots_directories_and_json_files(
    tmp_path: Path,
    target: str,
) -> None:
    real = _copy_example(tmp_path)
    if target == "root":
        link = tmp_path / "linked-memory"
        link.symlink_to(real, target_is_directory=True)
        candidate = link
    elif target == "sources":
        moved = tmp_path / "moved-sources"
        (real / "sources").rename(moved)
        (real / "sources").symlink_to(moved, target_is_directory=True)
        candidate = real
    else:
        original = real / "entries" / "fact-ensure-ascii.json"
        moved = tmp_path / "moved-entry.json"
        original.rename(moved)
        original.symlink_to(moved)
        candidate = real

    with pytest.raises(WorkspaceError, match="symlink|regular directory"):
        load_workspace(candidate)


@pytest.mark.parametrize("kind", ["nested", "non_json"])
def test_workspace_json_directories_are_flat_and_closed(
    tmp_path: Path,
    kind: str,
) -> None:
    root = _copy_example(tmp_path)
    if kind == "nested":
        (root / "entries" / "nested").mkdir()
    else:
        (root / "entries" / "notes.txt").write_text("ignored?", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="only flat .json"):
        load_workspace(root)


def test_duplicate_json_key_is_rejected_with_the_source_filename(tmp_path: Path) -> None:
    root = _copy_example(tmp_path)
    source = root / "sources" / "python-json-manual.json"
    source.write_text(
        '{"contract_version":"agiwiki.source.v1",'
        '"source_id":"src_22222222222222222222222222222222",'
        '"source_id":"src_33333333333333333333333333333333"}',
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceError, match="duplicate JSON object key: source_id"):
        load_workspace(root)


def test_workspace_rejects_parent_traversal_and_missing_collections(
    tmp_path: Path,
) -> None:
    root = _copy_example(tmp_path)
    with pytest.raises(WorkspaceError, match="parent traversal"):
        load_workspace(root / "child" / "..")

    shutil.rmtree(root / "sources")
    with pytest.raises(WorkspaceError, match="sources.*regular directory"):
        load_workspace(root)


def test_returned_entry_is_a_defensive_copy() -> None:
    workspace = load_workspace(EXAMPLE)
    entry = workspace.entry(CONCEPT_ID)
    entry["title"] = "changed"
    projected = workspace.entries
    projected[0]["title"] = "also changed"
    manifest = workspace.manifest
    manifest["title"] = "changed"

    assert workspace.entry(CONCEPT_ID)["title"] != "changed"
    assert all(item["title"] != "also changed" for item in workspace.entries)
    assert workspace.manifest["title"] != "changed"
