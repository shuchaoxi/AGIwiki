from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import agiwiki.doctor as doctor_module
from agiwiki.cli import main
from agiwiki.doctor import inspect_home
from agiwiki.home import HomeService
from agiwiki.integration import IntegrationError, render_windows_wsl_plan
from agiwiki.pack import build_workspace_pack
from agiwiki.paths import resolve_home_paths
from agiwiki.workspace import validate_workspace

EXAMPLE = Path(__file__).parents[1] / "examples" / "minimal-memory"


def _ready_home(tmp_path: Path) -> tuple[HomeService, str]:
    paths = resolve_home_paths({"AGIWIKI_HOME": str(tmp_path / "home")})
    home = HomeService(paths)
    home.init()
    pack = tmp_path / "pack"
    build_workspace_pack(validate_workspace(EXAMPLE), pack)
    release = home.install_pack(pack)
    home.activate(release.pack_id)
    return home, release.pack_id


def _snapshot(root: Path) -> dict[str, tuple[int, int, str | None]]:
    result: dict[str, tuple[int, int, str | None]] = {}
    for path in sorted([root, *root.rglob("*")]):
        current = os.stat(path, follow_symlinks=False)
        digest = None
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result[path.relative_to(root).as_posix() or "."] = (
            current.st_mode,
            current.st_mtime_ns,
            digest,
        )
    return result


@pytest.mark.parametrize("client", ["hermes", "claude", "codex"])
def test_windows_wsl_renderer_is_inert_and_argv_only(
    tmp_path: Path, client: str
) -> None:
    paths = resolve_home_paths({"AGIWIKI_HOME": str(tmp_path / "private-home")})
    plan = render_windows_wsl_plan(
        client=client,
        paths=paths,
        distro="Ubuntu-24.04",
        python_executable="/opt/agiwiki/bin/python",
    )

    assert plan["contract_version"] == "agiwiki.integration-plan.v1"
    assert plan["client"] == client
    assert plan["server"]["command"] == r"C:\Windows\System32\wsl.exe"
    assert plan["server"]["args"] == [
        "-d",
        "Ubuntu-24.04",
        "--exec",
        "/opt/agiwiki/bin/python",
        "-m",
        "agiwiki.mcp",
        "--home",
        str(paths.root),
    ]
    assert plan["install"]["argv"][0] == client
    assert plan["safety"]["renderer_writes_configuration"] is False
    assert plan["safety"]["server_is_read_only"] is True
    assert not paths.root.exists()


def test_windows_wsl_renderer_rejects_ambiguous_or_injected_values(
    tmp_path: Path,
) -> None:
    paths = resolve_home_paths({"AGIWIKI_HOME": str(tmp_path / "home")})
    with pytest.raises(IntegrationError, match="distribution"):
        render_windows_wsl_plan(
            client="hermes",
            paths=paths,
            distro="Ubuntu 24.04",
            python_executable="/opt/agiwiki/bin/python",
        )
    with pytest.raises(IntegrationError, match="server_name"):
        render_windows_wsl_plan(
            client="claude",
            paths=paths,
            server_name="agiwiki\nforged",
            distro="Ubuntu-24.04",
            python_executable="/opt/agiwiki/bin/python",
        )
    with pytest.raises(IntegrationError, match="absolute"):
        render_windows_wsl_plan(
            client="codex",
            paths=paths,
            distro="Ubuntu-24.04",
            python_executable="relative/python",
        )


def test_layered_doctor_missing_home_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = resolve_home_paths({"AGIWIKI_HOME": str(tmp_path / "missing")})
    monkeypatch.setattr(doctor_module, "_module_available", lambda _name: True)

    report = inspect_home(paths, platform="linux", environ={})

    assert report["contract_version"] == "agiwiki.doctor.v2"
    assert report["status"] == "NOT_READY"
    assert report["readiness"] == {
        "core": True,
        "artifact_memory": False,
        "mcp": False,
        "windows_wsl": None,
        "model_provider": "NOT_CHECKED",
    }
    assert {layer["name"]: layer["status"] for layer in report["layers"]}[
        "home"
    ] == "NOT_INITIALIZED"
    assert not paths.root.exists()
    core = next(layer for layer in report["layers"] if layer["name"] == "core")
    schemas = next(
        check for check in core["checks"] if check["name"] == "packaged_schemas"
    )
    assert schemas["schema_count"] == 23


def test_layered_doctor_ready_windows_wsl_does_not_mutate_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _ = _ready_home(tmp_path)
    bridge = tmp_path / "wsl.exe"
    bridge.write_bytes(b"test bridge placeholder")
    before = _snapshot(home.paths.root)
    monkeypatch.setattr(doctor_module, "_module_available", lambda _name: True)

    report = inspect_home(
        home.paths,
        platform="windows-wsl",
        distro="Ubuntu-24.04",
        environ={"WSL_DISTRO_NAME": "Ubuntu-24.04"},
        wsl_executable=bridge,
    )

    assert report["status"] == "READY"
    assert report["readiness"] == {
        "core": True,
        "artifact_memory": True,
        "mcp": True,
        "windows_wsl": True,
        "model_provider": "NOT_CHECKED",
    }
    assert _snapshot(home.paths.root) == before


def test_doctor_separates_missing_mcp_from_healthy_artifact_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _ = _ready_home(tmp_path)
    monkeypatch.setattr(doctor_module, "_module_available", lambda _name: False)

    report = inspect_home(home.paths, platform="linux", environ={})

    assert report["readiness"]["artifact_memory"] is True
    assert report["readiness"]["mcp"] is False
    assert report["status"] == "NOT_READY"
    layers = {layer["name"]: layer for layer in report["layers"]}
    assert layers["mcp"]["status"] == "ERROR"
    assert layers["model_provider"]["status"] == "NOT_CHECKED"


def test_doctor_reports_broken_pack_without_quarantining_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, pack_id = _ready_home(tmp_path)
    release = home.registry.get_release(pack_id)
    assert release is not None
    entry = next((home.release_path(release) / "entries").glob("*.json"))
    entry.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(doctor_module, "_module_available", lambda _name: True)

    report = inspect_home(home.paths, platform="linux", environ={})

    assert report["status"] == "NOT_READY"
    layers = {layer["name"]: layer for layer in report["layers"]}
    assert layers["packs"]["status"] == "ERROR"
    assert home.registry.get_release(pack_id)["health"] == "OK"
    assert home.registry.list_activations()[0]["pack_id"] == pack_id


def test_cli_renders_plan_and_doctor_without_writing_client_state(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    assert (
        main(
            [
                "--home",
                str(home),
                "integration",
                "render",
                "--client",
                "claude",
                "--distro",
                "Ubuntu-24.04",
                "--python-executable",
                "/opt/agiwiki/bin/python",
            ]
        )
        == 0
    )
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["client"] == "claude"
    assert rendered["install"]["writes_client_configuration"] is True
    assert not home.exists()

    monkeypatch.setattr(doctor_module, "_module_available", lambda _name: True)
    assert main(["--home", str(home), "doctor", "--platform", "linux"]) == 0
    diagnosed = json.loads(capsys.readouterr().out)
    assert diagnosed["status"] == "NOT_READY"
    assert not home.exists()
