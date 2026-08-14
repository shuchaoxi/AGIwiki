"""Install, verify, and activate immutable Packs in one personal Home."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Mapping, Sequence

from .codec import load_json_document, sha256_digest, write_json_new
from .pack import PackError, verify_pack
from .paths import (
    HomePaths,
    PathError,
    initialize_home_paths,
    resolve_home_paths,
    safe_child,
)
from .registry import HomeRegistry, RegistryError


PROJECT_CONTRACT = "agiwiki.project-pins.v1"


class HomeError(ValueError):
    """A Pack cannot be safely installed, activated, or resolved."""


@dataclass(frozen=True, slots=True)
class InstallReceipt:
    workspace_id: str
    pack_id: str
    manifest_digest: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class ActivationReceipt:
    workspace_id: str
    pack_id: str
    scope_type: str
    scope_key: str
    active: bool


class HomeService:
    def __init__(
        self,
        paths: HomePaths | None = None,
        *,
        verifier: Callable[[str | os.PathLike[str]], Mapping[str, Any]] = verify_pack,
    ):
        self.paths = resolve_home_paths() if paths is None else paths
        self.registry = HomeRegistry(self.paths)
        self._verify = verifier

    def init(self) -> dict[str, Any]:
        initialize_home_paths(self.paths)
        return self.registry.initialize()

    def install_pack(self, source: str | os.PathLike[str]) -> InstallReceipt:
        self.registry.metadata()
        source_path = Path(source).absolute()
        manifest = dict(self._verify(source_path))
        workspace_id = str(manifest["workspace_id"])
        pack_id = str(manifest["pack_id"])
        target_parent = safe_child(self.paths.packs_root, workspace_id)
        target_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(target_parent, 0o700)
        target = safe_child(target_parent, pack_id)
        relative = target.relative_to(self.paths.root).as_posix()
        release = {
            "pack_id": pack_id,
            "workspace_id": workspace_id,
            "version": str(manifest["workspace"]["version"]),
            "manifest_digest": str(manifest["manifest_digest"]),
            "relative_path": relative,
            "health": "OK",
        }
        if target.exists():
            existing = dict(self._verify(target))
            if _identity(existing) != _identity(manifest):
                raise HomeError("installed Pack path contains a conflicting identity")
            self.registry.insert_release(release)
            return InstallReceipt(workspace_id, pack_id, release["manifest_digest"], True)

        lock = safe_child(self.paths.staging_root, f"install-{pack_id}.lock")
        lock_fd = _acquire_lock(lock)
        temporary = Path(tempfile.mkdtemp(prefix=f"{pack_id}-", dir=self.paths.staging_root))
        published = False
        try:
            shutil.copytree(source_path, temporary / "pack", symlinks=False)
            staged = temporary / "pack"
            _make_private(staged)
            staged_manifest = dict(self._verify(staged))
            if _identity(staged_manifest) != _identity(manifest):
                raise HomeError("Pack changed while being installed")
            if target.exists():
                existing = dict(self._verify(target))
                if _identity(existing) != _identity(manifest):
                    raise HomeError("concurrent install published a conflicting Pack")
            else:
                os.rename(staged, target)
                published = True
            self.registry.insert_release(release)
            return InstallReceipt(
                workspace_id,
                pack_id,
                release["manifest_digest"],
                not published,
            )
        except (OSError, PackError, RegistryError, ValueError) as exc:
            if isinstance(exc, HomeError):
                raise
            raise HomeError("Pack installation failed") from exc
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
            os.close(lock_fd)
            lock.unlink(missing_ok=True)

    def verify_release(self, pack_id: str) -> dict[str, Any]:
        release = self.registry.get_release(pack_id)
        if release is None:
            raise HomeError("Pack is not installed")
        path = self.release_path(release)
        try:
            manifest = dict(self._verify(path))
            if _identity(manifest) != {
                "workspace_id": release["workspace_id"],
                "pack_id": release["pack_id"],
                "manifest_digest": release["manifest_digest"],
            }:
                raise HomeError("installed Pack identity does not match registry")
        except Exception as exc:
            self.registry.set_health(pack_id, "BROKEN")
            if isinstance(exc, HomeError):
                raise
            raise HomeError("installed Pack failed verification") from exc
        if release["health"] != "OK":
            self.registry.set_health(pack_id, "OK")
        return manifest

    def quarantine_release(self, pack_id: str) -> None:
        """Mark a failed installed Pack broken and remove every activation."""

        self.registry.set_health(pack_id, "BROKEN")

    def activate(
        self,
        pack_id: str,
        *,
        scope_type: str = "GLOBAL",
        scope_key: str = "global",
    ) -> ActivationReceipt:
        manifest = self.verify_release(pack_id)
        row = self.registry.activate_exact(
            pack_id=pack_id,
            scope_type=scope_type,
            scope_key=scope_key,
        )
        return ActivationReceipt(
            str(manifest["workspace_id"]),
            pack_id,
            row["scope_type"],
            row["scope_key"],
            True,
        )

    def deactivate(
        self,
        pack_id: str,
        *,
        scope_type: str = "GLOBAL",
        scope_key: str = "global",
    ) -> ActivationReceipt:
        release = self.registry.get_release(pack_id)
        if release is None:
            raise HomeError("Pack is not installed")
        self.registry.deactivate_exact(
            pack_id=pack_id,
            scope_type=scope_type,
            scope_key=scope_key,
        )
        return ActivationReceipt(
            release["workspace_id"],
            pack_id,
            scope_type.upper(),
            scope_key,
            False,
        )

    def list_releases(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in release.items() if key != "relative_path"}
            for release in self.registry.list_releases(workspace_id)
        ]

    def active_releases(
        self,
        *,
        scope_type: str = "GLOBAL",
        scope_key: str = "global",
        verify: bool = True,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for activation in self.registry.list_activations(
            scope_type=scope_type,
            scope_key=scope_key,
        ):
            row = {**activation, "pack_path": self.release_path(activation)}
            if verify:
                row["manifest"] = self.verify_release(activation["pack_id"])
            result.append(row)
        return result

    def release_path(self, release: Mapping[str, Any]) -> Path:
        relative = Path(str(release["relative_path"]))
        return safe_child(self.paths.root, *relative.parts)

    def link_project(
        self,
        project_root: str | os.PathLike[str],
        *,
        project_id: str,
        pack_ids: Sequence[str],
    ) -> dict[str, Any]:
        root = Path(project_root).absolute()
        try:
            marker_root = safe_child(root, ".agiwiki")
            destination = safe_child(marker_root, "project.json")
        except PathError as exc:
            raise HomeError("project root must not contain symlinks") from exc
        if not root.is_dir():
            raise HomeError("project root must be a regular directory")
        pins = sorted(set(pack_ids))
        marker_body = {
            "contract_version": PROJECT_CONTRACT,
            "project_id": project_id,
            "pack_ids": pins,
        }
        marker = {**marker_body, "marker_digest": sha256_digest(marker_body)}
        self.registry.replace_project_pins(
            project_id=project_id,
            marker_digest=marker["marker_digest"],
            pack_ids=pins,
        )
        if marker_root.is_symlink():
            raise HomeError("project marker directory must not be a symlink")
        marker_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(marker_root, 0o700)
        temporary = safe_child(marker_root, f".project-{os.getpid()}.json")
        temporary.unlink(missing_ok=True)
        write_json_new(temporary, marker)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        return marker

    def load_project_marker(
        self, project_root: str | os.PathLike[str]
    ) -> dict[str, Any]:
        root = Path(project_root).absolute()
        marker = load_json_document(root / ".agiwiki" / "project.json")
        if set(marker) != {
            "contract_version",
            "project_id",
            "pack_ids",
            "marker_digest",
        } or marker["contract_version"] != PROJECT_CONTRACT:
            raise HomeError("project marker fields are invalid")
        body = {key: marker[key] for key in marker if key != "marker_digest"}
        if marker["marker_digest"] != sha256_digest(body):
            raise HomeError("project marker digest mismatch")
        if not isinstance(marker["pack_ids"], list) or not marker["pack_ids"]:
            raise HomeError("project marker must pin at least one Pack")
        registered = self.registry.get_project(str(marker["project_id"]))
        expected = {
            "project_id": marker["project_id"],
            "marker_digest": marker["marker_digest"],
            "pack_ids": marker["pack_ids"],
        }
        if registered != expected:
            raise HomeError("project marker does not match its registered pins")
        return marker


Home = HomeService


def _identity(manifest: Mapping[str, Any]) -> dict[str, str]:
    return {
        "workspace_id": str(manifest["workspace_id"]),
        "pack_id": str(manifest["pack_id"]),
        "manifest_digest": str(manifest["manifest_digest"]),
    }


def _make_private(root: Path) -> None:
    for item in [root, *root.rglob("*")]:
        if item.is_symlink():
            raise HomeError("staged Pack must not contain symlinks")
        os.chmod(item, 0o700 if item.is_dir() else 0o600)


def _acquire_lock(path: Path) -> int:
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise HomeError("another Pack installation is already in progress") from exc


__all__ = [
    "ActivationReceipt",
    "Home",
    "HomeError",
    "HomeService",
    "InstallReceipt",
    "PROJECT_CONTRACT",
]
