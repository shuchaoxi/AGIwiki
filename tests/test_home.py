from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agiwiki.codec import sha256_digest
from agiwiki.home import HomeError, HomeService
from agiwiki.pack import build_workspace_pack
from agiwiki.paths import PathError, resolve_home_paths, safe_child
from agiwiki.workspace import validate_workspace

EXAMPLE = Path(__file__).parents[1] / "examples" / "minimal-memory"


def _home(tmp_path: Path) -> tuple[HomeService, Path, dict]:
    paths = resolve_home_paths({"AGIWIKI_HOME": str(tmp_path / "home")})
    home = HomeService(paths)
    home.init()
    pack = tmp_path / "pack"
    receipt = build_workspace_pack(validate_workspace(EXAMPLE), pack)
    return home, pack, receipt


def test_home_install_activate_replay_and_private_permissions(tmp_path: Path) -> None:
    home, pack, built = _home(tmp_path)

    first = home.install_pack(pack)
    replay = home.install_pack(pack)
    assert first.replayed is False
    assert replay.replayed is True
    assert first.pack_id == built["pack_id"]
    assert os.stat(home.paths.registry_db).st_mode & 0o077 == 0

    active = home.activate(first.pack_id)
    assert active.active is True
    rows = home.active_releases()
    assert [row["pack_id"] for row in rows] == [first.pack_id]
    assert "relative_path" not in home.list_releases()[0]

    stopped = home.deactivate(first.pack_id)
    assert stopped.active is False
    assert home.active_releases() == []


def test_tampered_installed_pack_is_broken_and_deactivated(tmp_path: Path) -> None:
    home, pack, _ = _home(tmp_path)
    installed = home.install_pack(pack)
    home.activate(installed.pack_id)
    release = home.registry.get_release(installed.pack_id)
    assert release is not None
    installed_root = home.release_path(release)
    target = next((installed_root / "entries").glob("*.json"))
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(HomeError, match="failed verification"):
        home.verify_release(installed.pack_id)
    assert home.registry.get_release(installed.pack_id)["health"] == "BROKEN"
    assert home.registry.list_activations() == []


def test_project_marker_is_exact_and_path_scope_is_contained(tmp_path: Path) -> None:
    home, pack, _ = _home(tmp_path)
    installed = home.install_pack(pack)
    project = tmp_path / "project"
    project.mkdir()

    marker = home.link_project(
        project,
        project_id="project-demo",
        pack_ids=[installed.pack_id],
    )
    assert home.load_project_marker(project) == marker
    assert os.stat(project / ".agiwiki" / "project.json").st_mode & 0o077 == 0
    with pytest.raises(PathError):
        safe_child(home.paths.root, "..", "escape")

    marker_path = project / ".agiwiki" / "project.json"
    forged_body = {
        "contract_version": marker["contract_version"],
        "project_id": "different-project",
        "pack_ids": marker["pack_ids"],
    }
    forged = {**forged_body, "marker_digest": sha256_digest(forged_body)}
    marker_path.write_text(json.dumps(forged), encoding="utf-8")
    with pytest.raises(HomeError, match="registered pins"):
        home.load_project_marker(project)


def test_home_path_rejects_symlink_component(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(PathError, match="symlink"):
        resolve_home_paths({"AGIWIKI_HOME": str(linked / "home")})


def test_link_project_rejects_symlink_parent_before_registry_write(
    tmp_path: Path,
) -> None:
    home, pack, _ = _home(tmp_path)
    installed = home.install_pack(pack)
    real_parent = tmp_path / "real-parent"
    project = real_parent / "project"
    project.mkdir(parents=True)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(HomeError, match="symlink"):
        home.link_project(
            linked_parent / "project",
            project_id="linked-project",
            pack_ids=[installed.pack_id],
        )

    assert home.registry.get_project("linked-project") is None
