"""Local resumable control plane for source-to-Workspace authoring."""

from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .authoring_contracts import (
    load_authoring_document,
    normalize_author_batch_result,
    seal_author_batch_result,
    validate_author_amendment,
    validate_author_batch_result,
    validate_author_budget_extension,
    validate_author_claim,
    validate_author_plan,
)
from .codec import (
    JSONDocumentError,
    canonical_json,
    file_sha256,
    load_json_document,
    sha256_digest,
    stable_id,
    write_json_new,
)
from .contracts import (
    MAX_JSON_BYTES,
    ContractError,
    normalize_entry,
    normalize_source,
    normalize_workspace,
)
from .quality import EntryQualityError, validate_entry_quality
from .workspace import WORKSPACE_MANIFEST, validate_workspace

AUTHORING_DIRECTORY = ".agiwiki-author"
AUTHOR_PLAN_CONTRACT = "agiwiki.author-plan.v2"
DEFAULT_PROMPT_SET = "agiwiki-author-memory.v4"
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_TEXT_SOURCE_BYTES = 64 * 1024 * 1024
MAX_AMENDMENTS = 100_000
MAX_AUTHOR_PLANS = 10_000
_PLAN_ID = re.compile(r"^authorplan_[a-f0-9]{32}$")
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


class AuthoringError(ValueError):
    """An authoring plan or progress transition is invalid."""


class AuthoringController:
    """Create immutable plans and derive progress from append-only receipts."""

    def plan(
        self,
        source: str | os.PathLike[str],
        *,
        workspace: str | os.PathLike[str],
        source_kind: str | None = None,
        title: str | None = None,
        edition: str | None = None,
        language: str | None = None,
        canonical_uri: str | None = None,
        unit_type: str = "auto",
        unit_count: int | None = None,
        batch_size: int | None = None,
        tokens_per_unit: int | None = None,
        budget_tokens: int | None = None,
        max_entries: int = 500,
        prompt_set_id: str = DEFAULT_PROMPT_SET,
    ) -> dict[str, Any]:
        root, manifest = _workspace_manifest(workspace)
        source_path = _source_path(source)
        source_size = _source_size(source_path)
        source_digest = file_sha256(source_path)
        effective_unit_type, effective_unit_count = _source_units(
            source_path,
            source_size=source_size,
            unit_type=unit_type,
            requested_count=unit_count,
        )
        effective_batch_size = _batch_size(effective_unit_type, batch_size)
        effective_tokens_per_unit = _tokens_per_unit(
            source_size,
            effective_unit_count,
            effective_unit_type,
            tokens_per_unit,
        )
        if budget_tokens is not None and (
            type(budget_tokens) is not int or not 256 <= budget_tokens <= 1_000_000_000
        ):
            raise AuthoringError(
                "budget_tokens must be null or between 256 and 1000000000"
            )
        if type(max_entries) is not int or not 1 <= max_entries <= 10_000:
            raise AuthoringError("max_entries must be between 1 and 10000")
        kind = source_kind or _source_kind(source_path)
        source_title = (title or source_path.stem).strip()
        if not source_title:
            raise AuthoringError("source title must contain text")
        source_identity = {
            "workspace_id": manifest["workspace_id"],
            "content_digest": source_digest,
            "title": source_title,
            "edition": edition,
        }
        source_id = stable_id("src", source_identity)
        source_document = _source_document(
            source_id=source_id,
            kind=kind,
            title=source_title,
            edition=edition,
            content_digest=source_digest,
            language=language,
            canonical_uri=canonical_uri,
        )
        seed = {
            "contract_version": AUTHOR_PLAN_CONTRACT,
            "workspace_id": manifest["workspace_id"],
            "source": {
                "source_id": source_id,
                "local_path": str(source_path),
                "content_digest": source_digest,
                "size_bytes": source_size,
                "kind": kind,
                "title": source_title,
                "edition": edition,
                "language": language,
                "canonical_uri": source_document["canonical_uri"],
                "unit_type": effective_unit_type,
                "unit_count": effective_unit_count,
            },
            "policy": {
                "prompt_set_id": prompt_set_id,
                "batch_size": effective_batch_size,
                "initial_budget_tokens": budget_tokens,
                "max_entries": max_entries,
                "tokens_per_unit": effective_tokens_per_unit,
            },
        }
        plan_id = stable_id("authorplan", seed)
        batches = _batches(
            plan_id,
            unit_type=effective_unit_type,
            unit_count=effective_unit_count,
            batch_size=effective_batch_size,
            tokens_per_unit=effective_tokens_per_unit,
        )
        body = {**seed, "plan_id": plan_id, "batches": batches}
        plan = validate_author_plan({**body, "plan_digest": sha256_digest(body)})
        _write_source(root, source_document)
        plan_root = _ensure_plan_directories(root, plan_id)
        plan_path = plan_root / "plan.json"
        replayed = _write_or_exact_replay(plan_path, plan, validate_author_plan)
        return {
            "contract_version": "agiwiki.author-plan-receipt.v1",
            "ok": True,
            "replayed": replayed,
            "plan_id": plan_id,
            "source_id": source_id,
            "batch_count": len(batches),
            "estimated_input_tokens": sum(
                item["estimated_input_tokens"] for item in batches
            ),
            "budget_tokens": budget_tokens,
            "plan_path": str(plan_path),
        }

    def next_batch(
        self,
        plan_id: str,
        *,
        workspace: str | os.PathLike[str],
    ) -> dict[str, Any]:
        root, plan, plan_root = self._load(plan_id, workspace)
        _verify_source(root, plan)
        progress = _progress(plan, plan_root)
        _verify_recorded_entry_bindings(root, plan, progress)
        outstanding = progress["outstanding_batch_id"]
        if outstanding is not None:
            batch = _batch(plan, outstanding)
            return _batch_delivery(plan, batch, progress, replayed=True)
        pending = [
            item
            for item in plan["batches"]
            if item["batch_id"] not in progress["result_batch_ids"]
        ]
        if not pending:
            return _complete_delivery(root, plan, progress)
        if progress["entry_count"] >= plan["policy"]["max_entries"]:
            return _stopped_delivery(plan, progress, "entry_limit")
        batch = pending[0]
        remaining = progress["remaining_budget_tokens"]
        if remaining is not None and batch["estimated_input_tokens"] > remaining:
            return _stopped_delivery(plan, progress, "budget_exhausted")
        claim_body = {
            "contract_version": "agiwiki.author-claim.v1",
            "claim_id": stable_id(
                "authorclaim",
                {"plan_id": plan["plan_id"], "batch_id": batch["batch_id"]},
            ),
            "plan_id": plan["plan_id"],
            "batch_id": batch["batch_id"],
        }
        claim = validate_author_claim(
            {**claim_body, "claim_digest": sha256_digest(claim_body)}
        )
        replayed = _write_or_exact_replay(
            plan_root / "claims" / f"{batch['batch_id']}.json",
            claim,
            validate_author_claim,
        )
        progress = _progress(plan, plan_root)
        return _batch_delivery(plan, batch, progress, replayed=replayed)

    def record(
        self,
        plan_id: str,
        result: Mapping[str, Any],
        *,
        workspace: str | os.PathLike[str],
    ) -> dict[str, Any]:
        root, plan, plan_root = self._load(plan_id, workspace)
        _verify_source(root, plan)
        candidate = normalize_author_batch_result(result)
        if candidate["plan_id"] != plan["plan_id"]:
            raise AuthoringError("batch result belongs to another plan")
        batch_ids = {item["batch_id"] for item in plan["batches"]}
        if candidate["batch_id"] not in batch_ids:
            raise AuthoringError("batch result references an unknown batch")
        claim_path = plan_root / "claims" / f"{candidate['batch_id']}.json"
        if not claim_path.is_file() or claim_path.is_symlink():
            raise AuthoringError("batch must be claimed before recording a result")
        result_path = plan_root / "results" / f"{candidate['batch_id']}.json"
        if result_path.exists() or result_path.is_symlink():
            existing = validate_author_batch_result(
                load_authoring_document(result_path)
            )
            if not _result_matches_request(existing, candidate):
                raise AuthoringError(
                    "batch result replay conflicts with stored content"
                )
            _verify_recorded_entry_bindings(
                root,
                plan,
                _progress(plan, plan_root),
            )
            return _record_receipt(plan, _progress(plan, plan_root), replayed=True)
        progress = _progress(plan, plan_root)
        _verify_recorded_entry_bindings(root, plan, progress)
        if progress["outstanding_batch_id"] != candidate["batch_id"]:
            raise AuthoringError("only the outstanding batch may be recorded")
        previous_entries = set(progress["entry_ids"])
        if previous_entries.intersection(candidate["entry_ids"]):
            raise AuthoringError("an Entry cannot be attributed to two batches")
        if (
            len(previous_entries) + len(candidate["entry_ids"])
            > plan["policy"]["max_entries"]
        ):
            raise AuthoringError("batch result exceeds the plan Entry limit")
        if candidate["outcome"] == "completed":
            workspace_snapshot = validate_workspace(root)
            by_id = {item["entry_id"]: item for item in workspace_snapshot.entries}
            missing = sorted(set(candidate["entry_ids"]) - set(by_id))
            if missing:
                raise AuthoringError(
                    "batch result references an Entry absent from Workspace"
                )
            source_id = plan["source"]["source_id"]
            batch = _batch(plan, candidate["batch_id"])
            for entry_id in candidate["entry_ids"]:
                if not _entry_cites_claimed_batch(
                    by_id[entry_id],
                    source_id=source_id,
                    batch=batch,
                ):
                    raise AuthoringError(
                        "every recorded Entry must cite a locator inside the claimed batch"
                    )
            entry_bindings = [
                {
                    "entry_id": entry_id,
                    "entry_digest": sha256_digest(by_id[entry_id]),
                }
                for entry_id in candidate["entry_ids"]
            ]
        else:
            entry_bindings = []
        sealed = seal_author_batch_result(candidate, entry_bindings)
        _write_or_exact_replay(
            result_path,
            sealed,
            validate_author_batch_result,
        )
        return _record_receipt(plan, _progress(plan, plan_root), replayed=False)

    def amend(
        self,
        plan_id: str,
        replacement: Mapping[str, Any],
        *,
        workspace: str | os.PathLike[str],
        entry_id: str,
        expected_old_digest: str,
        operation_id: str,
    ) -> dict[str, Any]:
        """Append one content transition, then atomically replace its Workspace Entry."""

        root, plan, plan_root = self._load(plan_id, workspace)
        _verify_source(root, plan)
        if _DIGEST.fullmatch(expected_old_digest) is None:
            raise AuthoringError("expected old Entry digest is invalid")
        candidate = normalize_entry(replacement)
        if candidate["entry_id"] != entry_id:
            raise AuthoringError("replacement Entry ID must match --entry-id")
        candidate_digest = sha256_digest(candidate)
        progress = _progress(plan, plan_root)
        owner = progress["entry_owners"].get(entry_id)
        if owner is None:
            raise AuthoringError("only an Entry from a completed batch may be amended")
        workspace_snapshot = validate_workspace(root)
        by_id = {item["entry_id"]: item for item in workspace_snapshot.entries}
        current = by_id.get(entry_id)
        if current is None:
            raise AuthoringError("recorded Entry is absent from Workspace")
        _validate_replacement(
            workspace_snapshot,
            candidate,
            source_id=plan["source"]["source_id"],
            batch=_batch(plan, owner),
        )
        current_digest = sha256_digest(current)
        amendment_id = stable_id(
            "authoramend",
            {"plan_id": plan["plan_id"], "operation_id": operation_id},
        )
        amendment_path = plan_root / "amendments" / f"{amendment_id}.json"
        existing = progress["amendments_by_id"].get(amendment_id)
        if existing is not None:
            if (
                existing["entry_id"] != entry_id
                or existing["old_entry_digest"] != expected_old_digest
                or existing["new_entry_digest"] != candidate_digest
            ):
                raise AuthoringError(
                    "author amendment replay conflicts with stored content"
                )
            replayed = True
            amendment = existing
        else:
            _verify_recorded_entry_bindings(root, plan, progress)
            effective_old = progress["effective_entry_digests"].get(entry_id)
            if effective_old is not None and effective_old != expected_old_digest:
                raise AuthoringError("expected old digest is not the effective binding")
            if current_digest != expected_old_digest:
                raise AuthoringError("current Entry does not match expected old digest")
            chain = progress["amendments_by_entry"].get(entry_id, ())
            previous = chain[-1] if chain else None
            result = progress["results_by_batch"][owner]
            basis = (
                "prior_amendment"
                if previous is not None
                else "recorded_result"
                if result["contract_version"] == "agiwiki.author-batch-result.v2"
                else "operator_asserted_legacy"
            )
            body = {
                "contract_version": "agiwiki.author-amendment.v1",
                "amendment_id": amendment_id,
                "plan_id": plan["plan_id"],
                "batch_id": owner,
                "base_result_digest": result["result_digest"],
                "entry_id": entry_id,
                "operation_id": operation_id,
                "sequence": len(chain) + 1,
                "previous_amendment_id": (
                    None if previous is None else previous["amendment_id"]
                ),
                "old_digest_basis": basis,
                "old_entry_digest": expected_old_digest,
                "new_entry_digest": candidate_digest,
            }
            amendment = validate_author_amendment(
                {**body, "amendment_digest": sha256_digest(body)}
            )
            _ensure_private_directory(plan_root / "amendments")
            _write_or_exact_replay(
                amendment_path,
                amendment,
                validate_author_amendment,
            )
            replayed = False

        if current_digest == amendment["old_entry_digest"]:
            _replace_entry_atomically(
                workspace_snapshot.locate_entry(entry_id),
                candidate,
                expected_old_digest=amendment["old_entry_digest"],
            )
        elif current_digest != amendment["new_entry_digest"]:
            raise AuthoringError(
                "current Entry matches neither side of the stored amendment"
            )

        validate_workspace(root)
        refreshed = _progress(plan, plan_root)
        _verify_recorded_entry_bindings(root, plan, refreshed)
        return {
            "contract_version": "agiwiki.author-amend-receipt.v1",
            "ok": True,
            "replayed": replayed,
            "plan_id": plan["plan_id"],
            "batch_id": owner,
            "entry_id": entry_id,
            "amendment_id": amendment_id,
            "old_entry_digest": amendment["old_entry_digest"],
            "new_entry_digest": amendment["new_entry_digest"],
        }

    def status(
        self,
        plan_id: str,
        *,
        workspace: str | os.PathLike[str],
    ) -> dict[str, Any]:
        root, plan, plan_root = self._load(plan_id, workspace)
        progress = _progress(plan, plan_root)
        source_ok = True
        recorded_entries_ok = True
        try:
            _verify_source(root, plan)
        except AuthoringError:
            source_ok = False
        if source_ok:
            try:
                _verify_recorded_entry_bindings(root, plan, progress)
            except AuthoringError:
                recorded_entries_ok = False
        return _status_receipt(
            plan,
            progress,
            source_ok=source_ok,
            recorded_entries_ok=recorded_entries_ok,
        )

    def build_preflight(
        self,
        *,
        workspace: str | os.PathLike[str],
    ) -> dict[str, Any]:
        """Report whether every local Author Plan is complete and internally bound.

        This is a local build guard, not semantic review. A Workspace with no Author
        Plans remains a valid manually authored input.
        """

        root = _workspace_root_for_build_preflight(workspace)
        plan_ids = _workspace_plan_ids(root)
        plans: list[dict[str, Any]] = []
        blockers: list[dict[str, str]] = []
        legacy_unsealed_entries = 0
        for plan_id in plan_ids:
            status = self.status(plan_id, workspace=root)
            legacy_unsealed_entries += status["legacy_unsealed_entry_count"]
            ready = True
            if status["completed_batches"] != status["batch_count"]:
                blockers.append({"plan_id": plan_id, "code": "INCOMPLETE_BATCHES"})
                ready = False
            if not status["source_ok"]:
                blockers.append({"plan_id": plan_id, "code": "SOURCE_CHANGED"})
                ready = False
            if not status["recorded_entries_ok"]:
                blockers.append({"plan_id": plan_id, "code": "RECORDED_ENTRY_DRIFT"})
                ready = False
            plans.append(
                {
                    "plan_id": plan_id,
                    "completed_batches": status["completed_batches"],
                    "batch_count": status["batch_count"],
                    "source_ok": status["source_ok"],
                    "recorded_entries_ok": status["recorded_entries_ok"],
                    "legacy_unsealed_entry_count": status[
                        "legacy_unsealed_entry_count"
                    ],
                    "ready": ready,
                }
            )
        return {
            "contract_version": "agiwiki.authoring-build-preflight.v1",
            "ready": not blockers,
            "plan_count": len(plans),
            "plans": plans,
            "blockers": blockers,
            "legacy_unsealed_entry_count": legacy_unsealed_entries,
            "semantic_review": "NOT_CHECKED",
        }

    def entry_status(
        self,
        plan_id: str,
        entry_id: str,
        *,
        workspace: str | os.PathLike[str],
    ) -> dict[str, Any]:
        """Report one plan-owned Entry binding without returning content or paths."""

        root, plan, plan_root = self._load(plan_id, workspace)
        _verify_source(root, plan)
        progress = _progress(plan, plan_root)
        batch_id = progress["entry_owners"].get(entry_id)
        if batch_id is None:
            raise AuthoringError("Entry is not recorded by this author plan")
        workspace_snapshot = validate_workspace(root)
        try:
            current = workspace_snapshot.entry(entry_id)
        except KeyError as exc:
            raise AuthoringError("recorded Entry is absent from Workspace") from exc
        chain = progress["amendments_by_entry"].get(entry_id, ())
        result = progress["results_by_batch"][batch_id]
        if result["contract_version"] == "agiwiki.author-batch-result.v2":
            binding_state = "sealed"
        elif chain:
            binding_state = "legacy_bridged"
        else:
            binding_state = "legacy_unsealed"
        return {
            "contract_version": "agiwiki.author-entry-status.v1",
            "plan_id": plan["plan_id"],
            "entry_id": entry_id,
            "batch_id": batch_id,
            "current_entry_digest": sha256_digest(current),
            "effective_entry_digest": progress["effective_entry_digests"].get(entry_id),
            "binding_state": binding_state,
            "latest_amendment_id": (None if not chain else chain[-1]["amendment_id"]),
            "amendment_count": len(chain),
        }

    def add_budget(
        self,
        plan_id: str,
        *,
        workspace: str | os.PathLike[str],
        added_tokens: int,
        operation_id: str,
    ) -> dict[str, Any]:
        root, plan, plan_root = self._load(plan_id, workspace)
        _verify_source(root, plan)
        if plan["policy"]["initial_budget_tokens"] is None:
            raise AuthoringError("an unlimited plan does not accept budget extensions")
        extension_id = stable_id(
            "authorext",
            {"plan_id": plan["plan_id"], "operation_id": operation_id},
        )
        extension_body = {
            "contract_version": "agiwiki.author-budget-extension.v1",
            "extension_id": extension_id,
            "plan_id": plan["plan_id"],
            "operation_id": operation_id,
            "added_tokens": added_tokens,
        }
        extension = validate_author_budget_extension(
            {
                **extension_body,
                "extension_digest": sha256_digest(extension_body),
            }
        )
        current = _progress(plan, plan_root)
        if current["total_budget_tokens"] + added_tokens > 1_000_000_000:
            raise AuthoringError("total authoring budget exceeds the supported limit")
        replayed = _write_or_exact_replay(
            plan_root / "budgets" / f"{extension_id}.json",
            extension,
            validate_author_budget_extension,
        )
        progress = _progress(plan, plan_root)
        return {
            "contract_version": "agiwiki.author-budget-receipt.v1",
            "ok": True,
            "replayed": replayed,
            "plan_id": plan["plan_id"],
            "total_budget_tokens": progress["total_budget_tokens"],
            "remaining_budget_tokens": progress["remaining_budget_tokens"],
        }

    def _load(
        self,
        plan_id: str,
        workspace: str | os.PathLike[str],
    ) -> tuple[Path, dict[str, Any], Path]:
        root, manifest = _workspace_manifest(workspace)
        if not isinstance(plan_id, str) or _PLAN_ID.fullmatch(plan_id) is None:
            raise AuthoringError("plan_id is invalid")
        plan_root = root / AUTHORING_DIRECTORY / "plans" / plan_id
        _require_private_directory(plan_root)
        plan = validate_author_plan(load_authoring_document(plan_root / "plan.json"))
        if (
            plan["plan_id"] != plan_id
            or plan["workspace_id"] != manifest["workspace_id"]
        ):
            raise AuthoringError("author plan identity does not match its Workspace")
        for name in ("claims", "results", "budgets"):
            _require_private_directory(plan_root / name)
        amendments = plan_root / "amendments"
        if amendments.exists() or amendments.is_symlink():
            _require_private_directory(amendments)
        return root, plan, plan_root


def _workspace_manifest(
    workspace: str | os.PathLike[str],
) -> tuple[Path, dict[str, Any]]:
    candidate = Path(workspace)
    if ".." in candidate.parts:
        raise AuthoringError("Workspace path must not contain parent traversal")
    root = candidate.absolute()
    _require_private_directory(root)
    try:
        manifest = normalize_workspace(
            load_json_document(root / WORKSPACE_MANIFEST, max_bytes=MAX_JSON_BYTES),
            source_path=root / WORKSPACE_MANIFEST,
        )
    except (ContractError, JSONDocumentError) as exc:
        raise AuthoringError("Workspace manifest is invalid") from exc
    return root, manifest


def _workspace_root_for_build_preflight(
    workspace: str | os.PathLike[str],
) -> Path:
    candidate = Path(workspace)
    if ".." in candidate.parts:
        raise AuthoringError("Workspace path must not contain parent traversal")
    root = candidate.absolute()
    try:
        current = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise AuthoringError("Workspace directory is unavailable") from exc
    if not stat.S_ISDIR(current.st_mode) or root.is_symlink():
        raise AuthoringError("Workspace must be a real directory")
    try:
        normalize_workspace(
            load_json_document(root / WORKSPACE_MANIFEST, max_bytes=MAX_JSON_BYTES),
            source_path=root / WORKSPACE_MANIFEST,
        )
    except (ContractError, JSONDocumentError) as exc:
        raise AuthoringError("Workspace manifest is invalid") from exc
    return root


def _workspace_plan_ids(root: Path) -> tuple[str, ...]:
    author_root = root / AUTHORING_DIRECTORY
    if not author_root.exists() and not author_root.is_symlink():
        return ()
    _require_private_directory(author_root)
    plans_root = author_root / "plans"
    _require_private_directory(plans_root)
    try:
        children = sorted(plans_root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise AuthoringError("authoring plan directory could not be read") from exc
    if len(children) > MAX_AUTHOR_PLANS:
        raise AuthoringError("Workspace has too many Author Plans")
    plan_ids: list[str] = []
    for child in children:
        if (
            child.is_symlink()
            or not child.is_dir()
            or _PLAN_ID.fullmatch(child.name) is None
        ):
            raise AuthoringError(
                "authoring plan directory contains an unexpected entry"
            )
        _require_private_directory(child)
        plan_ids.append(child.name)
    return tuple(plan_ids)


def _source_path(source: str | os.PathLike[str]) -> Path:
    candidate = Path(source)
    if ".." in candidate.parts or any(
        ord(character) < 32 for character in str(candidate)
    ):
        raise AuthoringError("Source path must not contain parent traversal")
    path = candidate.absolute()
    try:
        file_sha256(path)
    except JSONDocumentError as exc:
        raise AuthoringError("Source must be one stable regular file") from exc
    return path


def _source_size(path: Path) -> int:
    try:
        current = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise AuthoringError("Source metadata could not be read") from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or not 1 <= current.st_size <= MAX_SOURCE_BYTES
    ):
        raise AuthoringError("Source size is outside the supported range")
    return current.st_size


def _source_units(
    path: Path,
    *,
    source_size: int,
    unit_type: str,
    requested_count: int | None,
) -> tuple[str, int]:
    if unit_type == "auto":
        unit_type = (
            "page"
            if path.suffix.casefold() == ".pdf"
            else "line"
            if path.suffix.casefold() in {".md", ".markdown", ".txt"}
            else "file"
        )
    if unit_type not in {"page", "line", "file"}:
        raise AuthoringError("unit_type must be auto, page, line, or file")
    if unit_type == "page":
        if type(requested_count) is not int or not 1 <= requested_count <= 10_000_000:
            raise AuthoringError("page plans require --unit-count")
        return unit_type, requested_count
    if unit_type == "file":
        if requested_count not in {None, 1}:
            raise AuthoringError("file plans have exactly one unit")
        return unit_type, 1
    if source_size > MAX_TEXT_SOURCE_BYTES:
        raise AuthoringError("line plans are limited to 64 MiB UTF-8 sources")
    payload = _read_source(path, maximum=MAX_TEXT_SOURCE_BYTES)
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise AuthoringError("line plans require a UTF-8 source") from exc
    count = max(1, len(lines))
    if requested_count is not None and requested_count != count:
        raise AuthoringError("--unit-count does not match the UTF-8 line count")
    return unit_type, count


def _batch_size(unit_type: str, value: int | None) -> int:
    default = {"page": 20, "line": 500, "file": 1}[unit_type]
    result = default if value is None else value
    if type(result) is not int or not 1 <= result <= 100_000:
        raise AuthoringError("batch_size must be between 1 and 100000")
    if unit_type == "file" and result != 1:
        raise AuthoringError("file plans require batch_size=1")
    return result


def _tokens_per_unit(
    source_size: int,
    unit_count: int,
    unit_type: str,
    value: int | None,
) -> int:
    result = (
        value
        if value is not None
        else 800
        if unit_type == "page"
        else max(1, (source_size + 3 * unit_count - 1) // (3 * unit_count))
    )
    if type(result) is not int or not 1 <= result <= 1_000_000:
        raise AuthoringError("tokens_per_unit must be between 1 and 1000000")
    return result


def _batches(
    plan_id: str,
    *,
    unit_type: str,
    unit_count: int,
    batch_size: int,
    tokens_per_unit: int,
) -> list[dict[str, Any]]:
    locator_type = {"page": "page", "line": "line_range", "file": "file"}[unit_type]
    result: list[dict[str, Any]] = []
    for ordinal, start in enumerate(range(1, unit_count + 1, batch_size), start=1):
        end = min(unit_count, start + batch_size - 1)
        locator = {"type": locator_type, "start": start, "end": end}
        result.append(
            {
                "batch_id": stable_id(
                    "authorbatch",
                    {"plan_id": plan_id, "ordinal": ordinal, "locator": locator},
                ),
                "ordinal": ordinal,
                "locator": locator,
                "estimated_input_tokens": (end - start + 1) * tokens_per_unit,
            }
        )
    return result


def _source_kind(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".py", ".js", ".ts", ".java", ".go", ".rs", ".c", ".cpp"}:
        return "code"
    if suffix in {".md", ".markdown", ".txt"}:
        return "note"
    return "other"


def _source_document(
    *,
    source_id: str,
    kind: str,
    title: str,
    edition: str | None,
    content_digest: str,
    language: str | None,
    canonical_uri: str | None,
) -> dict[str, Any]:
    try:
        return normalize_source(
            {
                "contract_version": "agiwiki.source.v1",
                "source_id": source_id,
                "kind": kind,
                "title": title,
                "edition": edition,
                "content_digest": content_digest,
                "canonical_uri": canonical_uri,
                "language": language,
            }
        )
    except ContractError as exc:
        raise AuthoringError("planned Source metadata is invalid") from exc


def _write_source(root: Path, value: Mapping[str, Any]) -> None:
    _require_private_directory(root / "sources")
    target = root / "sources" / f"{value['source_id']}.json"
    if target.exists() or target.is_symlink():
        existing = normalize_source(
            load_json_document(target, max_bytes=MAX_JSON_BYTES)
        )
        if canonical_json(existing) != canonical_json(value):
            raise AuthoringError("planned Source conflicts with an existing Source")
        return
    write_json_new(target, value)


def _ensure_plan_directories(root: Path, plan_id: str) -> Path:
    author_root = root / AUTHORING_DIRECTORY
    plans_root = author_root / "plans"
    plan_root = plans_root / plan_id
    for directory in (author_root, plans_root, plan_root):
        _ensure_private_directory(directory)
    for name in ("claims", "results", "budgets", "amendments"):
        _ensure_private_directory(plan_root / name)
    return plan_root


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _require_private_directory(path)


def _require_private_directory(path: Path) -> None:
    try:
        current = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise AuthoringError("authoring directory is unavailable") from exc
    if (
        not stat.S_ISDIR(current.st_mode)
        or path.is_symlink()
        or current.st_mode & 0o077
    ):
        raise AuthoringError("authoring directories must be private real directories")


def _write_or_exact_replay(path: Path, value: Mapping[str, Any], validator) -> bool:
    try:
        write_json_new(path, value)
        return False
    except FileExistsError:
        existing = validator(load_authoring_document(path))
        if canonical_json(existing) != canonical_json(value):
            raise AuthoringError(
                "authoring idempotency key conflicts with stored content"
            )
        return True


def _load_plan_documents(
    plan: Mapping[str, Any], plan_root: Path, directory: str, validator
) -> dict[str, dict[str, Any]]:
    root = plan_root / directory
    expected_batches = {item["batch_id"] for item in plan["batches"]}
    result: dict[str, dict[str, Any]] = {}
    try:
        paths = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise AuthoringError("authoring progress directory could not be read") from exc
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise AuthoringError("authoring progress contains an unexpected file")
        document = validator(load_authoring_document(path))
        batch_id = document["batch_id"]
        if path.name != f"{batch_id}.json" or batch_id not in expected_batches:
            raise AuthoringError("authoring progress file identity is invalid")
        if document["plan_id"] != plan["plan_id"] or batch_id in result:
            raise AuthoringError("authoring progress belongs to another plan")
        result[batch_id] = document
    return result


def _load_budget_extensions(
    plan: Mapping[str, Any], plan_root: Path
) -> list[dict[str, Any]]:
    root = plan_root / "budgets"
    result: list[dict[str, Any]] = []
    operation_ids: set[str] = set()
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise AuthoringError("authoring budget contains an unexpected file")
        document = validate_author_budget_extension(load_authoring_document(path))
        if (
            path.name != f"{document['extension_id']}.json"
            or document["plan_id"] != plan["plan_id"]
            or document["operation_id"] in operation_ids
        ):
            raise AuthoringError("authoring budget extension identity is invalid")
        operation_ids.add(document["operation_id"])
        result.append(document)
    return result


def _result_entry_ids(result: Mapping[str, Any]) -> tuple[str, ...]:
    if result["contract_version"] == "agiwiki.author-batch-result.v2":
        return tuple(item["entry_id"] for item in result["entry_bindings"])
    return tuple(result["entry_ids"])


def _result_matches_request(
    result: Mapping[str, Any], request: Mapping[str, Any]
) -> bool:
    return {
        "plan_id": result["plan_id"],
        "batch_id": result["batch_id"],
        "outcome": result["outcome"],
        "measurement_source": result["measurement_source"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "entry_ids": sorted(_result_entry_ids(result)),
    } == {
        "plan_id": request["plan_id"],
        "batch_id": request["batch_id"],
        "outcome": request["outcome"],
        "measurement_source": request["measurement_source"],
        "input_tokens": request["input_tokens"],
        "output_tokens": request["output_tokens"],
        "entry_ids": sorted(request["entry_ids"]),
    }


def _load_amendments(
    plan: Mapping[str, Any],
    plan_root: Path,
    results: Mapping[str, Mapping[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, tuple[dict[str, Any], ...]],
]:
    root = plan_root / "amendments"
    if not root.exists() and not root.is_symlink():
        return {}, {}
    _require_private_directory(root)
    try:
        paths = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise AuthoringError("authoring amendments could not be read") from exc
    if len(paths) > MAX_AMENDMENTS:
        raise AuthoringError("authoring amendments exceed the supported limit")
    by_id: dict[str, dict[str, Any]] = {}
    operation_ids: set[str] = set()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            raise AuthoringError("authoring amendments contain an unexpected file")
        amendment = validate_author_amendment(load_authoring_document(path))
        amendment_id = amendment["amendment_id"]
        if (
            path.name != f"{amendment_id}.json"
            or amendment["plan_id"] != plan["plan_id"]
            or amendment_id in by_id
            or amendment["operation_id"] in operation_ids
        ):
            raise AuthoringError("authoring amendment identity is invalid")
        result = results.get(amendment["batch_id"])
        if (
            result is None
            or result["outcome"] != "completed"
            or amendment["base_result_digest"] != result["result_digest"]
            or amendment["entry_id"] not in _result_entry_ids(result)
        ):
            raise AuthoringError("authoring amendment is not bound to its batch result")
        by_id[amendment_id] = amendment
        operation_ids.add(amendment["operation_id"])
        grouped.setdefault(amendment["entry_id"], []).append(amendment)

    chains: dict[str, tuple[dict[str, Any], ...]] = {}
    for entry_id, unsorted in grouped.items():
        chain = sorted(unsorted, key=lambda item: item["sequence"])
        result = results[chain[0]["batch_id"]]
        base_bindings = {
            item["entry_id"]: item["entry_digest"]
            for item in result.get("entry_bindings", [])
        }
        previous: dict[str, Any] | None = None
        for sequence, amendment in enumerate(chain, start=1):
            if amendment["sequence"] != sequence:
                raise AuthoringError("authoring amendment sequence is not continuous")
            if amendment["batch_id"] != chain[0]["batch_id"]:
                raise AuthoringError("an amended Entry cannot move to another batch")
            if previous is None:
                if amendment["previous_amendment_id"] is not None:
                    raise AuthoringError(
                        "first author amendment cannot have a predecessor"
                    )
                if result["contract_version"] == "agiwiki.author-batch-result.v2":
                    if (
                        amendment["old_digest_basis"] != "recorded_result"
                        or amendment["old_entry_digest"] != base_bindings[entry_id]
                    ):
                        raise AuthoringError(
                            "first amendment does not continue the recorded Entry binding"
                        )
                elif amendment["old_digest_basis"] != "operator_asserted_legacy":
                    raise AuthoringError(
                        "legacy result amendment must declare its asserted baseline"
                    )
            elif (
                amendment["previous_amendment_id"] != previous["amendment_id"]
                or amendment["old_digest_basis"] != "prior_amendment"
                or amendment["old_entry_digest"] != previous["new_entry_digest"]
            ):
                raise AuthoringError("authoring amendment chain is invalid")
            previous = amendment
        chains[entry_id] = tuple(chain)
    return by_id, chains


def _progress(plan: Mapping[str, Any], plan_root: Path) -> dict[str, Any]:
    claims = _load_plan_documents(plan, plan_root, "claims", validate_author_claim)
    results = _load_plan_documents(
        plan, plan_root, "results", validate_author_batch_result
    )
    if set(results) - set(claims):
        raise AuthoringError("an authoring result exists without a claim")
    outstanding = sorted(set(claims) - set(results))
    if len(outstanding) > 1:
        raise AuthoringError("more than one authoring batch is outstanding")
    extensions = _load_budget_extensions(plan, plan_root)
    initial = plan["policy"]["initial_budget_tokens"]
    total_budget = (
        None
        if initial is None
        else initial + sum(item["added_tokens"] for item in extensions)
    )
    if total_budget is not None and total_budget > 1_000_000_000:
        raise AuthoringError("total authoring budget exceeds the supported limit")
    input_tokens = sum(item["input_tokens"] for item in results.values())
    output_tokens = sum(item["output_tokens"] for item in results.values())
    used_tokens = input_tokens + output_tokens
    entry_ids = sorted(
        entry_id for item in results.values() for entry_id in _result_entry_ids(item)
    )
    if len(entry_ids) != len(set(entry_ids)):
        raise AuthoringError("an Entry is attributed to more than one batch")
    amendments_by_id, amendments_by_entry = _load_amendments(plan, plan_root, results)
    entry_owners = {
        entry_id: batch_id
        for batch_id, result in results.items()
        for entry_id in _result_entry_ids(result)
    }
    recorded_digests = {
        binding["entry_id"]: binding["entry_digest"]
        for result in results.values()
        for binding in result.get("entry_bindings", [])
    }
    effective_digests = dict(recorded_digests)
    legacy_bridged = 0
    for entry_id, chain in amendments_by_entry.items():
        effective_digests[entry_id] = chain[-1]["new_entry_digest"]
        owner = entry_owners[entry_id]
        if results[owner]["contract_version"] == "agiwiki.author-batch-result.v1":
            legacy_bridged += 1
    legacy_total = sum(
        len(_result_entry_ids(result))
        for result in results.values()
        if result["contract_version"] == "agiwiki.author-batch-result.v1"
    )
    remaining = None if total_budget is None else max(0, total_budget - used_tokens)
    return {
        "claimed_batch_ids": set(claims),
        "result_batch_ids": set(results),
        "outstanding_batch_id": outstanding[0] if outstanding else None,
        "completed_batches": len(results),
        "batch_count": len(plan["batches"]),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "used_tokens": used_tokens,
        "total_budget_tokens": total_budget,
        "remaining_budget_tokens": remaining,
        "entry_ids": entry_ids,
        "entry_count": len(entry_ids),
        "entry_ids_by_batch": {
            batch_id: _result_entry_ids(result) for batch_id, result in results.items()
        },
        "entry_owners": entry_owners,
        "results_by_batch": results,
        "effective_entry_digests": effective_digests,
        "amendments_by_id": amendments_by_id,
        "amendments_by_entry": amendments_by_entry,
        "amendment_count": len(amendments_by_id),
        "record_sealed_entry_count": len(recorded_digests),
        "legacy_bridged_entry_count": legacy_bridged,
        "legacy_unsealed_entry_count": legacy_total - legacy_bridged,
        "skipped_batches": sum(
            result["outcome"] == "skipped" for result in results.values()
        ),
    }


def _batch(plan: Mapping[str, Any], batch_id: str) -> dict[str, Any]:
    for item in plan["batches"]:
        if item["batch_id"] == batch_id:
            return dict(item)
    raise AuthoringError("authoring batch is absent from its plan")


def _batch_delivery(
    plan: Mapping[str, Any],
    batch: Mapping[str, Any],
    progress: Mapping[str, Any],
    *,
    replayed: bool,
) -> dict[str, Any]:
    return {
        "contract_version": "agiwiki.author-batch-delivery.v1",
        "status": "batch_ready",
        "replayed": replayed,
        "plan_id": plan["plan_id"],
        "batch_id": batch["batch_id"],
        "source_id": plan["source"]["source_id"],
        "source_path": plan["source"]["local_path"],
        "source_digest": plan["source"]["content_digest"],
        "locator": dict(batch["locator"]),
        "estimated_input_tokens": batch["estimated_input_tokens"],
        "prompt_set_id": plan["policy"]["prompt_set_id"],
        "remaining_budget_tokens": progress["remaining_budget_tokens"],
        "max_entries_remaining": plan["policy"]["max_entries"]
        - progress["entry_count"],
        "result_seed": {
            "contract_version": "agiwiki.author-batch-result.v1",
            "plan_id": plan["plan_id"],
            "batch_id": batch["batch_id"],
        },
    }


def _stopped_delivery(
    plan: Mapping[str, Any], progress: Mapping[str, Any], reason: str
) -> dict[str, Any]:
    return {
        "contract_version": "agiwiki.author-batch-delivery.v1",
        "status": reason,
        "plan_id": plan["plan_id"],
        "remaining_budget_tokens": progress["remaining_budget_tokens"],
        "entry_count": progress["entry_count"],
        "completed_batches": progress["completed_batches"],
        "batch_count": progress["batch_count"],
    }


def _complete_delivery(
    root: Path, plan: Mapping[str, Any], progress: Mapping[str, Any]
) -> dict[str, Any]:
    workspace_valid = False
    if progress["entry_count"]:
        validate_workspace(root)
        workspace_valid = True
    return {
        "contract_version": "agiwiki.author-batch-delivery.v1",
        "status": "complete" if workspace_valid else "complete_without_entries",
        "plan_id": plan["plan_id"],
        "workspace_valid": workspace_valid,
        "entry_count": progress["entry_count"],
        "completed_batches": progress["completed_batches"],
        "batch_count": progress["batch_count"],
    }


def _record_receipt(
    plan: Mapping[str, Any], progress: Mapping[str, Any], *, replayed: bool
) -> dict[str, Any]:
    return {
        "contract_version": "agiwiki.author-record-receipt.v1",
        "ok": True,
        "replayed": replayed,
        "plan_id": plan["plan_id"],
        "completed_batches": progress["completed_batches"],
        "batch_count": progress["batch_count"],
        "entry_count": progress["entry_count"],
        "used_tokens": progress["used_tokens"],
        "remaining_budget_tokens": progress["remaining_budget_tokens"],
    }


def _status_receipt(
    plan: Mapping[str, Any],
    progress: Mapping[str, Any],
    *,
    source_ok: bool,
    recorded_entries_ok: bool,
) -> dict[str, Any]:
    completed = progress["completed_batches"]
    batch_count = progress["batch_count"]
    return {
        "contract_version": "agiwiki.author-status.v2",
        "plan_id": plan["plan_id"],
        "source_ok": source_ok,
        "recorded_entries_ok": recorded_entries_ok,
        "batch_count": batch_count,
        "completed_batches": completed,
        "remaining_batches": batch_count - completed,
        "progress_basis_points": completed * 10_000 // batch_count,
        "outstanding_batch_id": progress["outstanding_batch_id"],
        "entry_count": progress["entry_count"],
        "recorded_entries_digest_bound": (progress["legacy_unsealed_entry_count"] == 0),
        "record_sealed_entry_count": progress["record_sealed_entry_count"],
        "legacy_bridged_entry_count": progress["legacy_bridged_entry_count"],
        "legacy_unsealed_entry_count": progress["legacy_unsealed_entry_count"],
        "amendment_count": progress["amendment_count"],
        "skipped_batches": progress["skipped_batches"],
        "input_tokens": progress["input_tokens"],
        "output_tokens": progress["output_tokens"],
        "used_tokens": progress["used_tokens"],
        "total_budget_tokens": progress["total_budget_tokens"],
        "remaining_budget_tokens": progress["remaining_budget_tokens"],
        "estimated_remaining_input_tokens": sum(
            item["estimated_input_tokens"]
            for item in plan["batches"]
            if item["batch_id"] not in progress["result_batch_ids"]
        ),
    }


def _verify_recorded_entry_bindings(
    root: Path,
    plan: Mapping[str, Any],
    progress: Mapping[str, Any],
) -> None:
    if not progress["entry_ids"]:
        return
    try:
        workspace = validate_workspace(root)
    except (ContractError, JSONDocumentError) as exc:
        raise AuthoringError(
            "recorded Entries no longer form a valid Workspace"
        ) from exc
    by_id = {item["entry_id"]: item for item in workspace.entries}
    source_id = plan["source"]["source_id"]
    for batch_id, entry_ids in progress["entry_ids_by_batch"].items():
        batch = _batch(plan, batch_id)
        for entry_id in entry_ids:
            entry = by_id.get(entry_id)
            if entry is None or not _entry_cites_claimed_batch(
                entry,
                source_id=source_id,
                batch=batch,
            ):
                raise AuthoringError(
                    "a recorded Entry is missing or no longer cites its claimed batch"
                )
            expected_digest = progress["effective_entry_digests"].get(entry_id)
            if expected_digest is not None and sha256_digest(entry) != expected_digest:
                raise AuthoringError(
                    "a recorded Entry no longer matches its effective content binding"
                )


def _validate_replacement(
    workspace,
    replacement: Mapping[str, Any],
    *,
    source_id: str,
    batch: Mapping[str, Any],
) -> None:
    entry_id = replacement["entry_id"]
    if entry_id not in workspace.entry_paths:
        raise AuthoringError("replacement Entry must already exist in Workspace")
    source_ids = {item["source_id"] for item in workspace.sources}
    entry_ids = {item["entry_id"] for item in workspace.entries}
    for reference in replacement["source_refs"]:
        if reference["source_id"] not in source_ids:
            raise AuthoringError("replacement Entry references an unknown Source")
    for relation in replacement["relations"]:
        target = relation["target_entry_id"]
        if target not in entry_ids or target == entry_id:
            raise AuthoringError("replacement Entry relation is invalid")
    try:
        validate_entry_quality(replacement)
    except EntryQualityError as exc:
        raise AuthoringError(str(exc)) from exc
    if not _entry_cites_claimed_batch(
        replacement,
        source_id=source_id,
        batch=batch,
    ):
        raise AuthoringError(
            "replacement Entry must still cite a locator inside its original batch"
        )


def _replace_entry_atomically(
    target: Path,
    replacement: Mapping[str, Any],
    *,
    expected_old_digest: str,
) -> None:
    if target.is_symlink() or not target.is_file():
        raise AuthoringError("Workspace Entry target is not a regular file")
    current = normalize_entry(load_json_document(target, max_bytes=MAX_JSON_BYTES))
    if sha256_digest(current) != expected_old_digest:
        raise AuthoringError("Workspace Entry changed before atomic replacement")
    payload = (canonical_json(dict(replacement)) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".agiwiki-amend-",
        suffix=".json.tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("canonical JSON replacement made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        current = normalize_entry(load_json_document(target, max_bytes=MAX_JSON_BYTES))
        if sha256_digest(current) != expected_old_digest:
            raise AuthoringError("Workspace Entry changed before atomic replacement")
        os.replace(temporary, target)
        try:
            parent_descriptor = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        except OSError:
            pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()


def _entry_cites_claimed_batch(
    entry: Mapping[str, Any],
    *,
    source_id: str,
    batch: Mapping[str, Any],
) -> bool:
    batch_locator = batch["locator"]
    for source_ref in entry["source_refs"]:
        if source_ref["source_id"] != source_id:
            continue
        if batch_locator["type"] == "file":
            return True
        locator = source_ref["locator"]
        if locator["type"] != batch_locator["type"]:
            continue
        bounds = _numeric_locator_bounds(locator["value"])
        if bounds is None:
            continue
        start, end = bounds
        if batch_locator["start"] <= start <= end <= batch_locator["end"]:
            return True
    return False


def _numeric_locator_bounds(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?", value.strip())
    if match is None:
        return None
    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    return (start, end) if start <= end else None


def _verify_source(root: Path, plan: Mapping[str, Any]) -> None:
    path = _source_path(plan["source"]["local_path"])
    if (
        _source_size(path) != plan["source"]["size_bytes"]
        or file_sha256(path) != plan["source"]["content_digest"]
    ):
        raise AuthoringError("planned Source changed; create a new plan")
    source_path = root / "sources" / f"{plan['source']['source_id']}.json"
    try:
        registered = normalize_source(
            load_json_document(source_path, max_bytes=MAX_JSON_BYTES)
        )
    except (ContractError, JSONDocumentError) as exc:
        raise AuthoringError("planned Source registration is invalid") from exc
    expected = _source_document(
        source_id=plan["source"]["source_id"],
        kind=plan["source"]["kind"],
        title=plan["source"]["title"],
        edition=plan["source"]["edition"],
        content_digest=plan["source"]["content_digest"],
        language=plan["source"]["language"],
        canonical_uri=plan["source"].get("canonical_uri"),
    )
    if canonical_json(registered) != canonical_json(expected):
        raise AuthoringError("planned Source registration changed")


def _read_source(path: Path, *, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AuthoringError("Source could not be opened safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise AuthoringError("Source is not a bounded regular file")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(payload) > maximum or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise AuthoringError("Source changed while it was being read")
    return bytes(payload)


__all__ = [
    "AUTHORING_DIRECTORY",
    "AUTHOR_PLAN_CONTRACT",
    "DEFAULT_PROMPT_SET",
    "AuthoringController",
    "AuthoringError",
]
