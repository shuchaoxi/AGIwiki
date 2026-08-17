from __future__ import annotations

from pathlib import Path

import pytest

import agiwiki.index as index_module
from agiwiki.home import HomeService
from agiwiki.pack import PackError, build_workspace_pack, verify_pack
from agiwiki.paths import resolve_home_paths
from agiwiki.runtime import (
    MemoryRuntime,
    _estimated_tokens,
)
from agiwiki.runtime import (
    RuntimeError as MemoryRuntimeError,
)
from agiwiki.workspace import validate_workspace

EXAMPLE = Path(__file__).parents[1] / "examples" / "minimal-memory"


def _runtime(tmp_path: Path) -> tuple[MemoryRuntime, str, HomeService]:
    home = HomeService(resolve_home_paths({"AGIWIKI_HOME": str(tmp_path / "home")}))
    home.init()
    pack = tmp_path / "pack"
    workspace = validate_workspace(EXAMPLE)
    build_workspace_pack(workspace, pack)
    assert verify_pack(pack)["workspace_digest"] == workspace.workspace_digest
    installed = home.install_pack(pack)
    home.activate(installed.pack_id)
    return MemoryRuntime(home), installed.pack_id, home


def test_find_then_exact_get_returns_portable_source_metadata(tmp_path: Path) -> None:
    runtime, pack_id, _ = _runtime(tmp_path)

    found = runtime.find_memory("canonical JSON")
    assert found["found"] is True
    assert "query" not in found
    candidate = found["results"][0]
    exact = runtime.get_memory(candidate["entry_id"], pack_id=pack_id)
    assert exact["found"] is True
    assert exact["entry_version_id"] == candidate["entry_version_id"]
    assert exact["sources"][0]["canonical_uri"].startswith("https://")
    assert "path" not in str(exact).lower()

    missing = runtime.find_memory("definitely absent material 987654")
    assert missing["found"] is False
    assert (
        runtime.get_memory("entry_99999999999999999999999999999999")["found"] is False
    )


def test_project_marker_can_only_narrow_global_activation(tmp_path: Path) -> None:
    runtime, active_pack, home = _runtime(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    home.link_project(project, project_id="demo", pack_ids=[active_pack])
    assert runtime.catalog(project_root=project)["count"] == 1

    assert runtime.catalog(workspace_ids=[])["count"] == 0
    assert runtime.find_memory("canonical JSON", workspace_ids=[])["found"] is False


def test_hot_find_and_exact_get_do_not_repeat_full_pack_verification(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, pack_id, home = _runtime(tmp_path)
    runtime.find_memory("canonical JSON")  # Build the disposable cache once.

    find_calls = 0
    original = index_module.verify_pack

    def count_find(path):
        nonlocal find_calls
        find_calls += 1
        return original(path)

    monkeypatch.setattr(index_module, "verify_pack", count_find)
    assert runtime.find_memory("canonical JSON")["found"] is True
    assert find_calls == 1

    get_calls = 0

    def count_get(path):
        nonlocal get_calls
        get_calls += 1
        return original(path)

    home._verify = count_get
    assert (
        runtime.get_memory("entry_44444444444444444444444444444444", pack_id=pack_id)[
            "found"
        ]
        is True
    )
    assert get_calls == 1


def test_find_quarantines_a_pack_that_fails_integrity(tmp_path: Path) -> None:
    runtime, pack_id, home = _runtime(tmp_path)
    runtime.find_memory("canonical JSON")
    release = home.registry.get_release(pack_id)
    assert release is not None
    target = next((home.release_path(release) / "entries").glob("*.json"))
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(PackError):
        runtime.find_memory("canonical JSON")
    assert home.registry.get_release(pack_id)["health"] == "BROKEN"
    assert home.registry.list_activations() == []


def test_get_input_validation_does_not_depend_on_active_state(tmp_path: Path) -> None:
    home = HomeService(
        resolve_home_paths({"AGIWIKI_HOME": str(tmp_path / "empty-home")})
    )
    home.init()
    runtime = MemoryRuntime(home)
    with pytest.raises(MemoryRuntimeError, match="entry_id"):
        runtime.get_memory("not-an-entry-id")


def test_token_estimate_is_conservative_for_cjk_and_reports_truncation(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, pack_id, _ = _runtime(tmp_path)
    candidate = {
        "entry_version_id": "entryv_" + "1" * 32,
        "entry_id": "entry_" + "2" * 32,
        "title": "很长的候选记忆",
        "summary": "知识" * 400,
        "kind": "concept",
        "score": 1.0,
    }
    assert _estimated_tokens(candidate) >= 800

    def oversized(*args, **kwargs):
        return {
            "pack_id": pack_id,
            "manifest_digest": runtime.home.registry.get_release(pack_id)[
                "manifest_digest"
            ],
            "results": [candidate],
        }

    monkeypatch.setattr("agiwiki.runtime.find_pack_memory", oversized)
    result = runtime.find_memory("知识", token_budget=256)
    assert result["found"] is True
    assert result["returned"] is False
    assert result["candidate_count"] == 1
    assert result["truncated_by_budget"] is True
