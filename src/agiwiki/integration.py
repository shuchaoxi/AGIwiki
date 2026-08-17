"""Render inert client configuration plans for the local stdio MCP server."""

from __future__ import annotations

import os
import re
import sys
import sysconfig
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .paths import HomePaths

INTEGRATION_CONTRACT = "agiwiki.integration-plan.v1"
SKILL_PATH_CONTRACT = "agiwiki.skill-path.v1"


class IntegrationError(ValueError):
    """A requested client integration cannot be rendered safely."""


_CLIENTS = frozenset({"hermes", "claude", "codex"})
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$|^[a-z]$")
_SAFE_DISTRO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_WSL_COMMAND = r"C:\Windows\System32\wsl.exe"
_SKILLS = {
    "read": {
        "name": "agiwiki-memory",
        "required": ("SKILL.md", "agents/openai.yaml"),
    },
    "author": {
        "name": "agiwiki-author-memory",
        "required": (
            "SKILL.md",
            "agents/openai.yaml",
            "references/authoring-contract.md",
        ),
    },
    "review": {
        "name": "agiwiki-critical-review",
        "required": (
            "SKILL.md",
            "agents/openai.yaml",
            "references/review-contract.md",
        ),
    },
}


def locate_skill(capability: str) -> dict[str, Any]:
    """Locate one complete bundled Skill without copying or installing it."""

    if capability not in _SKILLS:
        raise IntegrationError("capability must be read, author, or review")
    definition = _SKILLS[capability]
    skill_name = definition["name"]
    candidates = (
        (
            "source_checkout",
            Path(__file__).resolve().parents[2] / "skills" / skill_name,
        ),
        (
            "installed_data",
            Path(sysconfig.get_path("data"))
            / "share"
            / "agiwiki"
            / "skills"
            / skill_name,
        ),
    )
    selected_source, selected_path = candidates[-1]
    missing = list(definition["required"])
    for source, candidate in candidates:
        absent = [
            relative
            for relative in definition["required"]
            if not (candidate / relative).is_file()
        ]
        if not absent:
            selected_source, selected_path, missing = source, candidate, []
            break
    available = not missing
    return {
        "contract_version": SKILL_PATH_CONTRACT,
        "capability": capability,
        "skill_name": skill_name,
        "status": "AVAILABLE" if available else "MISSING",
        "available": available,
        "source": selected_source,
        "path": str(selected_path),
        "entrypoint": str(selected_path / "SKILL.md"),
        "required_files": list(definition["required"]),
        "missing_files": missing,
        "side_effects": {
            "files_written": False,
            "agent_configuration_modified": False,
        },
    }


def render_windows_wsl_plan(
    *,
    client: str,
    paths: HomePaths,
    server_name: str = "agiwiki",
    distro: str | None = None,
    python_executable: str | os.PathLike[str] | None = None,
    claude_scope: str = "local",
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return argv-only setup instructions without writing client configuration."""

    selected_client = _client(client)
    name = _server_name(server_name)
    values = os.environ if environ is None else environ
    selected_distro = distro or values.get("WSL_DISTRO_NAME")
    if not isinstance(selected_distro, str) or not _SAFE_DISTRO.fullmatch(
        selected_distro
    ):
        raise IntegrationError("distro must be an explicit safe WSL distribution name")
    executable = _absolute_wsl_path(
        sys.executable if python_executable is None else python_executable,
        "python executable",
    )
    home = _absolute_wsl_path(paths.root, "AGIWiki Home")
    server_args = [
        "-d",
        selected_distro,
        "--exec",
        executable,
        "-m",
        "agiwiki.mcp",
        "--home",
        home,
    ]
    install, verify, remove, verification_scope = _client_commands(
        selected_client,
        name,
        server_args,
        claude_scope=claude_scope,
    )
    return {
        "contract_version": INTEGRATION_CONTRACT,
        "status": "SUPPORTED",
        "client": selected_client,
        "platform": "windows-wsl",
        "transport": "stdio",
        "server_name": name,
        "server": {"command": _WSL_COMMAND, "args": server_args},
        "install": {"argv": install, "writes_client_configuration": True},
        "verify": {"argv": verify, "scope": verification_scope},
        "remove": {"argv": remove},
        "safety": {
            "renderer_writes_configuration": False,
            "server_is_read_only": True,
            "server_uses_network": False,
            "contains_local_paths": True,
        },
        "limitations": [
            "Run doctor before executing the generated install argv.",
            "Client model authentication and provider connectivity are not tested.",
            "ChatGPT desktop does not consume this local stdio configuration.",
            "Do not publish the rendered output because it contains local paths.",
        ],
    }


def _client_commands(
    client: str,
    name: str,
    server_args: list[str],
    *,
    claude_scope: str,
) -> tuple[list[str], list[str], list[str], str]:
    server = [_WSL_COMMAND, *server_args]
    if client == "hermes":
        return (
            [
                "hermes",
                "mcp",
                "add",
                name,
                "--command",
                _WSL_COMMAND,
                "--args",
                *server_args,
            ],
            ["hermes", "mcp", "test", name],
            ["hermes", "mcp", "remove", name],
            "connects and discovers tools",
        )
    if client == "claude":
        if claude_scope not in {"local", "project", "user"}:
            raise IntegrationError("claude_scope must be local, project, or user")
        return (
            [
                "claude",
                "mcp",
                "add",
                "--scope",
                claude_scope,
                name,
                "--",
                *server,
            ],
            ["claude", "mcp", "get", name],
            ["claude", "mcp", "remove", name, "--scope", claude_scope],
            "connects and reports server health",
        )
    return (
        ["codex", "mcp", "add", name, "--", *server],
        ["codex", "mcp", "get", name],
        ["codex", "mcp", "remove", name],
        "validates stored configuration; model execution is a separate test",
    )


def _client(value: str) -> str:
    if not isinstance(value, str) or value not in _CLIENTS:
        raise IntegrationError("client must be hermes, claude, or codex")
    return value


def _server_name(value: str) -> str:
    if not isinstance(value, str) or _SAFE_NAME.fullmatch(value) is None:
        raise IntegrationError(
            "server_name must be a lowercase alphanumeric name with optional hyphens"
        )
    return value


def _absolute_wsl_path(value: str | os.PathLike[str], label: str) -> str:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or any(char in raw for char in "\x00\r\n"):
        raise IntegrationError(f"{label} is invalid")
    path = PurePosixPath(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise IntegrationError(f"{label} must be an absolute contained WSL path")
    normalized = path.as_posix()
    if normalized != raw.rstrip("/") and not (raw == "/" and normalized == "/"):
        raise IntegrationError(f"{label} must already be normalized")
    return normalized


__all__ = [
    "INTEGRATION_CONTRACT",
    "SKILL_PATH_CONTRACT",
    "IntegrationError",
    "locate_skill",
    "render_windows_wsl_plan",
]
