"""Deterministic Git and subprocess gates for the development loop."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import selectors
import signal
import stat as stat_module
import subprocess
import time
from typing import Iterable, Sequence

from .contracts import VerificationReceipt


class GateError(RuntimeError):
    """A deterministic development gate could not be evaluated safely."""


MAX_VERIFICATION_OUTPUT_BYTES = 1024 * 1024
_OUTPUT_READ_BYTES = 64 * 1024


def git_root(path: Path) -> Path:
    result = _run_git(path, "rev-parse", "--show-toplevel")
    root = Path(result.stdout.strip()).resolve()
    if root != path.resolve():
        raise GateError("repo_root must be the Git worktree root")
    return root


def head_commit(repo: Path) -> str:
    return _run_git(repo, "rev-parse", "HEAD").stdout.strip()


def dirty_paths(repo: Path) -> list[str]:
    """Return all tracked and untracked changes as normalized repository paths."""

    outputs = [
        _run_git(repo, "diff", "--name-only", "-z").stdout_bytes,
        _run_git(repo, "diff", "--cached", "--name-only", "-z").stdout_bytes,
        _run_git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout_bytes,
    ]
    paths: set[str] = set()
    for output in outputs:
        for raw in output.split(b"\0"):
            if raw:
                paths.add(normalize_repo_path(os.fsdecode(raw)))
    return sorted(paths)


def snapshot(repo: Path, paths: Iterable[str]) -> dict[str, str]:
    return {path: _path_digest(repo, path) for path in sorted(set(paths))}


def changed_since_snapshot(
    repo: Path, baseline: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    current_paths = set(dirty_paths(repo)) | set(baseline)
    current = snapshot(repo, current_paths)
    changed = sorted(
        path for path in current_paths if current.get(path) != baseline.get(path)
    )
    return changed, current


def worktree_diff_digest(repo: Path) -> str:
    """Hash the exact tracked diff plus the contents of untracked files."""

    digest = hashlib.sha256()
    for args in (
        ("diff", "--binary", "HEAD"),
        ("diff", "--cached", "--binary", "HEAD"),
    ):
        result = _run_git(repo, *args)
        digest.update(result.stdout_bytes)
    tracked = set(
        normalize_repo_path(os.fsdecode(item))
        for item in _run_git(
            repo, "ls-files", "--others", "--exclude-standard", "-z"
        ).stdout_bytes.split(b"\0")
        if item
    )
    for path in sorted(tracked):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_path_digest(repo, path).encode("ascii"))
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def outside_allowlist(paths: Iterable[str], patterns: Sequence[str]) -> list[str]:
    normalized_patterns = [normalize_allow_pattern(pattern) for pattern in patterns]
    return sorted(
        path
        for path in set(paths)
        if not any(path_matches(path, pattern) for pattern in normalized_patterns)
    )


def path_matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(f"{prefix}/")
    return path == pattern


def normalize_repo_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GateError("repository path must be non-empty text")
    candidate = value.replace("\\", "/")
    path = PurePosixPath(candidate)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise GateError("repository path must be normalized and relative")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise GateError("repository path must identify a file")
    return normalized


def normalize_allow_pattern(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GateError("allow pattern must be non-empty text")
    candidate = value.replace("\\", "/")
    recursive = candidate.endswith("/**")
    base = candidate[:-3].rstrip("/") if recursive else candidate
    if any(character in base for character in "*?[") or "**" in base:
        raise GateError("allowlist supports only exact paths and directory/**")
    normalized = normalize_repo_path(base)
    return f"{normalized}/**" if recursive else normalized


def run_acceptance_commands(
    repo: Path,
    commands: Sequence[Sequence[str]],
    *,
    timeout_seconds: float,
) -> list[VerificationReceipt]:
    receipts: list[VerificationReceipt] = []
    for raw_command in commands:
        command = _normalize_command(raw_command)
        returncode, timed_out, truncated, output_digest, output_bytes = (
            _run_bounded_command(
                repo,
                command,
                timeout_seconds=timeout_seconds,
            )
        )
        receipts.append(
            _verification_receipt(
                command,
                returncode=returncode,
                timed_out=timed_out,
                truncated=truncated,
                output_digest=output_digest,
                output_bytes=output_bytes,
            )
        )
    return receipts


def _path_digest(repo: Path, relative: str) -> str:
    path = repo / relative
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return "missing"
    mode = f"{stat_module.S_IFMT(stat_result.st_mode):o}:{stat_module.S_IMODE(stat_result.st_mode):o}"
    if stat_module.S_ISLNK(stat_result.st_mode):
        target_digest = hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
        return f"symlink:{mode}:{target_digest}"
    if not stat_module.S_ISREG(stat_result.st_mode):
        return f"non-file:{mode}"
    content_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"file:{mode}:{content_digest}"


def _run_bounded_command(
    repo: Path,
    command: list[str],
    *,
    timeout_seconds: float,
) -> tuple[int | None, bool, bool, str, int]:
    """Run one command in a killable session with bounded captured output."""

    process = subprocess.Popen(
        command,
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        shell=False,
        start_new_session=True,
    )
    if process.stdout is None:  # pragma: no cover - fixed Popen configuration
        _kill_process_group(process)
        raise GateError("verification output pipe was not created")

    digest = hashlib.sha256()
    output_bytes = 0
    timed_out = False
    truncated = False
    output_eof = False
    deadline = time.monotonic() + timeout_seconds
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while not output_eof:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                timed_out = True
                _kill_process_group(process)
                break

            events = selector.select(timeout=min(remaining_seconds, 0.1))
            for key, _events in events:
                chunk = os.read(key.fd, _OUTPUT_READ_BYTES)
                if not chunk:
                    output_eof = True
                    break
                remaining_bytes = MAX_VERIFICATION_OUTPUT_BYTES - output_bytes
                accepted = chunk[:remaining_bytes]
                digest.update(accepted)
                output_bytes += len(accepted)
                if len(accepted) != len(chunk):
                    truncated = True
                    _kill_process_group(process)
                    break
            if truncated:
                break
        if process.poll() is None:
            process.wait()
    finally:
        selector.close()
        process.stdout.close()
        if process.poll() is None:
            _kill_process_group(process)
            process.wait()

    return (
        None if timed_out else process.returncode,
        timed_out,
        truncated,
        f"sha256:{digest.hexdigest()}",
        output_bytes,
    )


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the session created for a verifier, including its descendants."""

    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class _GitResult:
    def __init__(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self.returncode = result.returncode
        self.stdout_bytes = result.stdout
        self.stderr_bytes = result.stderr
        self.stdout = os.fsdecode(result.stdout)
        self.stderr = os.fsdecode(result.stderr)


def _run_git(repo: Path, *arguments: str) -> _GitResult:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo,
            check=False,
            capture_output=True,
            timeout=30,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateError("Git command failed") from exc
    result = _GitResult(completed)
    if result.returncode != 0:
        raise GateError("Git command failed")
    return result


def _normalize_command(raw: Sequence[str]) -> list[str]:
    if isinstance(raw, (str, bytes)) or not raw:
        raise GateError("acceptance command must be a non-empty argv list")
    command: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise GateError("acceptance command arguments must be non-empty text")
        command.append(item)
    return command


def _verification_receipt(
    command: list[str],
    *,
    returncode: int | None,
    timed_out: bool,
    truncated: bool,
    output_digest: str,
    output_bytes: int,
) -> VerificationReceipt:
    command_bytes = json.dumps(
        command, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return {
        "command_digest": f"sha256:{hashlib.sha256(command_bytes).hexdigest()}",
        "argv_count": len(command),
        "returncode": returncode,
        "timed_out": timed_out,
        "truncated": truncated,
        "output_digest": output_digest,
        "output_bytes": output_bytes,
    }


__all__ = [
    "GateError",
    "MAX_VERIFICATION_OUTPUT_BYTES",
    "changed_since_snapshot",
    "dirty_paths",
    "git_root",
    "head_commit",
    "normalize_allow_pattern",
    "normalize_repo_path",
    "outside_allowlist",
    "path_matches",
    "run_acceptance_commands",
    "snapshot",
    "worktree_diff_digest",
]
