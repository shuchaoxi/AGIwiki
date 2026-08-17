"""Small operator CLI for the personal factual-memory lifecycle."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .adaptive import (
    AdaptiveMemoryStore,
    generate_local_capability,
    load_local_capability,
    write_local_capability,
)
from .adaptive_contracts import (
    MAX_ADAPTIVE_INPUT_BYTES,
    load_adaptive_input,
    parse_adaptive_input,
)
from .authoring import DEFAULT_PROMPT_SET, AuthoringController
from .authoring_contracts import load_authoring_document
from .codec import load_json_document
from .contracts import MAX_JSON_BYTES
from .doctor import inspect_home
from .home import HomeService
from .integration import locate_skill, render_windows_wsl_plan
from .mcp import main as mcp_main
from .pack import build_workspace_pack, verify_pack
from .paths import resolve_home_paths
from .quality import ENTRY_QUALITY_POLICY
from .runtime import MemoryRuntime
from .workspace import initialize_workspace, validate_workspace


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.area == "mcp":
        forwarded = ["--home", str(_paths(args.home).root)]
        if args.project is not None:
            forwarded.extend(("--project", args.project))
        return mcp_main(forwarded)
    try:
        result = _dispatch(args)
    except (OSError, ValueError, KeyError) as exc:
        print(
            json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}),
            file=sys.stderr,
        )
        return 2
    if result is not None:
        print(json.dumps(_jsonable(result), ensure_ascii=False, sort_keys=True))
    return 0


def _dispatch(args: argparse.Namespace) -> Any:
    if args.area == "workspace" and args.action == "init":
        manifest = initialize_workspace(
            args.path,
            slug=args.slug,
            title=args.title,
            default_locale=args.locale,
            version=args.version,
        )
        return {
            "ok": True,
            "workspace_id": manifest["workspace_id"],
            "slug": manifest["slug"],
            "version": manifest["version"],
        }
    if args.area == "workspace" and args.action == "validate":
        workspace = validate_workspace(args.path)
        return {
            "ok": True,
            "quality_policy": ENTRY_QUALITY_POLICY,
            "workspace_id": workspace.workspace_id,
            "workspace_digest": workspace.workspace_digest,
            "source_count": len(workspace.sources),
            "entry_count": len(workspace.entries),
        }
    if args.area == "pack" and args.action == "build":
        workspace = validate_workspace(args.workspace)
        preflight = AuthoringController().build_preflight(workspace=args.workspace)
        if not preflight["ready"] and not args.allow_incomplete_authoring:
            codes = ",".join(item["code"] for item in preflight["blockers"])
            raise ValueError(
                "authoring preflight blocked Pack build; finish or repair every "
                f"Author Plan before retrying ({codes})"
            )
        receipt = build_workspace_pack(workspace, args.destination)
        return {
            **receipt,
            "authoring_preflight": preflight,
            "incomplete_authoring_override": bool(
                args.allow_incomplete_authoring and not preflight["ready"]
            ),
        }
    if args.area == "pack" and args.action == "verify":
        manifest = verify_pack(args.path)
        return {
            "ok": True,
            "workspace_id": manifest["workspace_id"],
            "pack_id": manifest["pack_id"],
            "manifest_digest": manifest["manifest_digest"],
            "entry_count": manifest["entry_count"],
        }
    if args.area == "author":
        controller = AuthoringController()
        if args.action == "plan":
            return controller.plan(
                args.source,
                workspace=args.workspace,
                source_kind=args.source_kind,
                title=args.title,
                edition=args.edition,
                language=args.language,
                canonical_uri=args.canonical_uri,
                unit_type=args.unit_type,
                unit_count=args.unit_count,
                batch_size=args.batch_size,
                tokens_per_unit=args.tokens_per_unit,
                budget_tokens=args.budget_tokens,
                max_entries=args.max_entries,
                prompt_set_id=args.prompt_set_id,
            )
        if args.action == "next":
            return controller.next_batch(args.plan_id, workspace=args.workspace)
        if args.action == "record":
            return controller.record(
                args.plan_id,
                load_authoring_document(args.input),
                workspace=args.workspace,
            )
        if args.action == "amend":
            return controller.amend(
                args.plan_id,
                load_json_document(args.input, max_bytes=MAX_JSON_BYTES),
                workspace=args.workspace,
                entry_id=args.entry_id,
                expected_old_digest=args.expect_old_digest,
                operation_id=args.operation_id,
            )
        if args.action == "status":
            return controller.status(args.plan_id, workspace=args.workspace)
        if args.action == "entry-status":
            return controller.entry_status(
                args.plan_id,
                args.entry_id,
                workspace=args.workspace,
            )
        if args.action == "add-budget":
            return controller.add_budget(
                args.plan_id,
                workspace=args.workspace,
                added_tokens=args.tokens,
                operation_id=args.operation_id,
            )
    paths = _paths(args.home)
    home = HomeService(paths)
    if args.area == "home":
        if args.action == "init":
            return {"ok": True, **home.init()}
        if args.action == "install":
            return home.install_pack(args.pack)
        if args.action == "activate":
            return home.activate(args.pack_id)
        if args.action == "deactivate":
            return home.deactivate(args.pack_id)
        if args.action == "list":
            return {"ok": True, "releases": home.list_releases()}
        if args.action == "link-project":
            return home.link_project(
                args.project,
                project_id=args.project_id,
                pack_ids=args.pack_ids,
            )
    if args.area == "adaptive":
        home.registry.metadata()
        adaptive = AdaptiveMemoryStore(paths)
        if args.action == "init":
            return {"ok": True, **adaptive.initialize()}
        adaptive.metadata()
        if args.action == "principal-create":
            if args.confirm is not True:
                raise ValueError("principal creation requires explicit confirmation")
            principal_id, token = generate_local_capability(args.principal_id)
            write_local_capability(
                args.credential_output,
                principal_id=principal_id,
                token=token,
            )
            try:
                receipt = adaptive.create_principal(
                    token=token,
                    permissions=args.permissions,
                    confirm=True,
                    principal_id=principal_id,
                )
            except Exception:
                Path(args.credential_output).unlink(missing_ok=True)
                raise
            return {**receipt, "credential_written": True}
        if args.action == "principal-revoke":
            return adaptive.revoke_principal(
                args.principal_id,
                confirm=args.confirm,
            )
        if args.action == "remember":
            return adaptive.remember(
                _adaptive_input(args.input),
                operation_id=args.operation_id,
            )
        if args.action == "get":
            return adaptive.get(
                args.memory_id,
                scope_type=args.scope_type,
                scope_key=args.scope_key,
            )
        if args.action == "list":
            return adaptive.list(
                scope_type=args.scope_type,
                scope_key=args.scope_key,
                memory_class=args.memory_class,
                status=args.status,
                limit=args.limit,
                include_expired=args.include_expired,
            )
        if args.action == "search":
            return adaptive.search(
                args.query,
                scope_type=args.scope_type,
                scope_key=args.scope_key,
                memory_class=args.memory_class,
                limit=args.limit,
            )
        if args.action == "review-plan":
            return adaptive.review_plan(
                scope_type=args.scope_type,
                scope_key=args.scope_key,
                limit=args.limit,
            )
        if args.action == "review-due":
            return adaptive.review_due(
                scope_type=args.scope_type,
                scope_key=args.scope_key,
                interval=args.interval,
            )
        if args.action == "review-create":
            principal_id, credential = load_local_capability(args.credential_file)
            return adaptive.create_review_proposal(
                scope_type=args.scope_type,
                scope_key=args.scope_key,
                principal_id=principal_id,
                credential=credential,
                limit=args.limit,
            )
        if args.action == "review-decide":
            principal_id, credential = load_local_capability(args.credential_file)
            return adaptive.decide_review(
                args.proposal_id,
                _adaptive_input(args.input),
                scope_type=args.scope_type,
                scope_key=args.scope_key,
                principal_id=principal_id,
                credential=credential,
                confirm=args.confirm,
                decision_id=args.decision_id,
            )
        if args.action == "review-apply":
            principal_id, credential = load_local_capability(args.credential_file)
            return adaptive.apply_review_decision(
                args.decision_id,
                _adaptive_input(args.input),
                scope_type=args.scope_type,
                scope_key=args.scope_key,
                principal_id=principal_id,
                credential=credential,
                confirm=args.confirm,
                application_id=args.application_id,
            )
        if args.action == "review-show":
            return adaptive.show_review(
                args.proposal_id,
                scope_type=args.scope_type,
                scope_key=args.scope_key,
            )
        if args.action == "correct":
            return adaptive.correct(
                args.memory_id,
                _adaptive_input(args.input),
                scope_type=args.scope_type,
                scope_key=args.scope_key,
                operation_id=args.operation_id,
            )
        if args.action == "forget":
            return adaptive.forget(
                args.memory_id,
                scope_type=args.scope_type,
                scope_key=args.scope_key,
                confirm=args.confirm,
                operation_id=args.operation_id,
            )
    if args.area == "integration" and args.action == "render":
        if args.platform != "windows-wsl":
            raise ValueError("only the windows-wsl integration is implemented")
        return render_windows_wsl_plan(
            client=args.client,
            paths=paths,
            server_name=args.name,
            distro=args.distro,
            python_executable=args.python_executable,
            claude_scope=args.claude_scope,
        )
    if args.area == "integration" and args.action == "skill-path":
        return locate_skill(args.capability)
    runtime = MemoryRuntime(home)
    if args.area == "memory":
        if args.action == "catalog":
            return runtime.catalog(project_root=args.project)
        if args.action == "find":
            return runtime.find_memory(
                args.query,
                project_root=args.project,
                limit=args.limit,
                token_budget=args.token_budget,
            )
        if args.action == "get":
            return runtime.get_memory(
                args.entry_id,
                entry_version_id=args.entry_version_id,
                pack_id=args.pack_id,
                project_root=args.project,
            )
    if args.area == "doctor":
        return inspect_home(paths, platform=args.platform, distro=args.distro)
    raise ValueError("unsupported command")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agiwiki")
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument("--home", help="override the private AGIWiki Home")
    areas = parser.add_subparsers(dest="area", required=True)

    workspace = areas.add_parser("workspace", help="create or validate a Workspace")
    workspace_actions = workspace.add_subparsers(dest="action", required=True)
    initialize = workspace_actions.add_parser(
        "init", help="create an empty authoring Workspace"
    )
    initialize.add_argument("path")
    initialize.add_argument("--slug", required=True)
    initialize.add_argument("--title", required=True)
    initialize.add_argument("--locale", default="zh-CN")
    initialize.add_argument("--version", default="0.1.0")
    validate = workspace_actions.add_parser("validate", help="validate one Workspace")
    validate.add_argument("path")

    pack = areas.add_parser("pack", help="build or verify immutable Memory Packs")
    pack_actions = pack.add_subparsers(dest="action", required=True)
    build = pack_actions.add_parser("build", help="build a Pack from a Workspace")
    build.add_argument("workspace")
    build.add_argument("destination")
    build.add_argument(
        "--allow-incomplete-authoring",
        action="store_true",
        help="explicitly build despite incomplete or drifted local Author Plans",
    )
    verify = pack_actions.add_parser("verify", help="verify a closed Pack")
    verify.add_argument("path")

    author = areas.add_parser(
        "author", help="plan and resume source-to-Workspace authoring batches"
    )
    author_actions = author.add_subparsers(dest="action", required=True)
    author_plan = author_actions.add_parser(
        "plan", help="create an immutable local authoring plan"
    )
    author_plan.add_argument("source")
    author_plan.add_argument("--workspace", required=True)
    author_plan.add_argument(
        "--source-kind", choices=("pdf", "web", "manual", "code", "note", "other")
    )
    author_plan.add_argument("--title")
    author_plan.add_argument("--edition")
    author_plan.add_argument("--language")
    author_plan.add_argument("--canonical-uri")
    author_plan.add_argument(
        "--unit-type", choices=("auto", "page", "line", "file"), default="auto"
    )
    author_plan.add_argument("--unit-count", type=int)
    author_plan.add_argument("--batch-size", type=int)
    author_plan.add_argument("--tokens-per-unit", type=int)
    author_plan.add_argument("--budget-tokens", type=int)
    author_plan.add_argument("--max-entries", type=int, default=500)
    author_plan.add_argument("--prompt-set-id", default=DEFAULT_PROMPT_SET)
    author_next = author_actions.add_parser(
        "next", help="claim or replay the next bounded source batch"
    )
    author_next.add_argument("plan_id")
    author_next.add_argument("--workspace", required=True)
    author_record = author_actions.add_parser(
        "record", help="record one validated batch result"
    )
    author_record.add_argument("plan_id")
    author_record.add_argument("--workspace", required=True)
    author_record.add_argument("--input", required=True)
    author_amend = author_actions.add_parser(
        "amend", help="append one reviewed Entry replacement"
    )
    author_amend.add_argument("plan_id")
    author_amend.add_argument("--workspace", required=True)
    author_amend.add_argument("--entry-id", required=True)
    author_amend.add_argument("--input", required=True)
    author_amend.add_argument("--expect-old-digest", required=True)
    author_amend.add_argument("--operation-id", required=True)
    author_status = author_actions.add_parser(
        "status", help="show source, budget, and batch progress"
    )
    author_status.add_argument("plan_id")
    author_status.add_argument("--workspace", required=True)
    author_entry_status = author_actions.add_parser(
        "entry-status", help="show one recorded Entry binding without its content"
    )
    author_entry_status.add_argument("plan_id")
    author_entry_status.add_argument("--workspace", required=True)
    author_entry_status.add_argument("--entry-id", required=True)
    author_budget = author_actions.add_parser(
        "add-budget", help="idempotently extend a bounded plan budget"
    )
    author_budget.add_argument("plan_id")
    author_budget.add_argument("--workspace", required=True)
    author_budget.add_argument("--tokens", type=int, required=True)
    author_budget.add_argument("--operation-id", required=True)

    home = areas.add_parser("home", help="manage the private local Home")
    home_actions = home.add_subparsers(dest="action", required=True)
    home_actions.add_parser("init", help="initialize private Home storage")
    install = home_actions.add_parser("install", help="install a verified Pack")
    install.add_argument("pack")
    activate = home_actions.add_parser("activate", help="activate one exact Pack")
    activate.add_argument("pack_id")
    deactivate = home_actions.add_parser("deactivate", help="deactivate one exact Pack")
    deactivate.add_argument("pack_id")
    home_actions.add_parser("list", help="list installed Pack releases")
    link = home_actions.add_parser(
        "link-project", help="pin project scope without broadening activation"
    )
    link.add_argument("project")
    link.add_argument("project_id")
    link.add_argument("pack_ids", nargs="+")

    memory = areas.add_parser("memory", help="query active factual memory")
    memory_actions = memory.add_subparsers(dest="action", required=True)
    catalog = memory_actions.add_parser("catalog", help="list active Packs")
    catalog.add_argument("--project")
    find = memory_actions.add_parser("find", help="find candidate memories")
    find.add_argument("query")
    find.add_argument("--project")
    find.add_argument("--limit", type=int, default=8)
    find.add_argument("--token-budget", type=int, default=3000)
    get = memory_actions.add_parser("get", help="read one exact memory Entry")
    get.add_argument("entry_id")
    get.add_argument("--entry-version-id")
    get.add_argument("--pack-id")
    get.add_argument("--project")

    adaptive = areas.add_parser(
        "adaptive", help="manage explicitly authorized local adaptive memory"
    )
    adaptive_actions = adaptive.add_subparsers(dest="action", required=True)
    adaptive_actions.add_parser(
        "init", help="explicitly initialize the private adaptive-memory ledger"
    )
    principal_create = adaptive_actions.add_parser(
        "principal-create",
        help="enroll a local review capability and write its secret to a private file",
    )
    principal_create.add_argument(
        "--permissions",
        nargs="+",
        required=True,
        choices=("review_propose", "review_approve", "review_apply"),
    )
    principal_create.add_argument("--principal-id")
    principal_create.add_argument("--credential-output", required=True)
    principal_create.add_argument("--confirm", action="store_true")
    principal_revoke = adaptive_actions.add_parser(
        "principal-revoke", help="revoke future use of one local review capability"
    )
    principal_revoke.add_argument("principal_id")
    principal_revoke.add_argument("--confirm", action="store_true")
    remember = adaptive_actions.add_parser(
        "remember", help="store one closed adaptive-memory request"
    )
    remember.add_argument("--input", required=True, help="JSON file or - for stdin")
    remember.add_argument(
        "--operation-id",
        help="reuse one op_<32 lowercase hex> identifier for safe request replay",
    )
    adaptive_get = adaptive_actions.add_parser("get", help="read one active memory")
    adaptive_get.add_argument("memory_id")
    _adaptive_scope_arguments(adaptive_get)
    adaptive_list = adaptive_actions.add_parser(
        "list", help="list memories in one exact scope"
    )
    _adaptive_scope_arguments(adaptive_list)
    adaptive_list.add_argument("--memory-class", choices=("profile", "episode"))
    adaptive_list.add_argument(
        "--status",
        choices=("active", "superseded", "deleted"),
        default="active",
    )
    adaptive_list.add_argument("--limit", type=int, default=50)
    adaptive_list.add_argument(
        "--include-expired",
        action="store_true",
        help="include expired active memories for owner management",
    )
    adaptive_search = adaptive_actions.add_parser(
        "search", help="literal-search active memories in one exact scope"
    )
    adaptive_search.add_argument("query")
    _adaptive_scope_arguments(adaptive_search)
    adaptive_search.add_argument("--memory-class", choices=("profile", "episode"))
    adaptive_search.add_argument("--limit", type=int, default=8)
    review_plan = adaptive_actions.add_parser(
        "review-plan", help="produce a content-free read-only review proposal"
    )
    _adaptive_scope_arguments(review_plan)
    review_plan.add_argument("--limit", type=int, default=100)
    review_due = adaptive_actions.add_parser(
        "review-due",
        help="read review timing and pending-action reminders without mutation",
    )
    _adaptive_scope_arguments(review_due)
    review_due.add_argument("--interval", choices=("daily", "weekly"), default="weekly")
    review_create = adaptive_actions.add_parser(
        "review-create",
        help="persist one content-free exact-scope review proposal",
    )
    _adaptive_scope_arguments(review_create)
    review_create.add_argument("--limit", type=int, default=100)
    review_create.add_argument("--credential-file", required=True)
    review_decide = adaptive_actions.add_parser(
        "review-decide",
        help="record a confirmed human decision without applying its actions",
    )
    review_decide.add_argument("proposal_id")
    review_decide.add_argument(
        "--input", required=True, help="JSON file or - for stdin"
    )
    review_decide.add_argument("--credential-file", required=True)
    review_decide.add_argument(
        "--decision-id",
        help="reuse one decision_<32 lowercase hex> identifier for safe replay",
    )
    review_decide.add_argument(
        "--confirm",
        action="store_true",
        help="confirm recording the immutable decision receipt",
    )
    _adaptive_scope_arguments(review_decide)
    review_apply = adaptive_actions.add_parser(
        "review-apply",
        help="apply supported accepted actions with a separate local capability",
    )
    review_apply.add_argument("decision_id")
    review_apply.add_argument("--input", required=True, help="JSON file or - for stdin")
    review_apply.add_argument("--credential-file", required=True)
    review_apply.add_argument(
        "--application-id",
        help="reuse one application_<32 lowercase hex> identifier for safe replay",
    )
    review_apply.add_argument(
        "--confirm",
        action="store_true",
        help="confirm the destructive application of the accepted actions",
    )
    _adaptive_scope_arguments(review_apply)
    review_show = adaptive_actions.add_parser(
        "review-show",
        help="show one exact-scope proposal and its optional decision receipt",
    )
    review_show.add_argument("proposal_id")
    _adaptive_scope_arguments(review_show)
    correct = adaptive_actions.add_parser(
        "correct", help="append a correction to one active memory"
    )
    correct.add_argument("memory_id")
    correct.add_argument("--input", required=True, help="JSON file or - for stdin")
    correct.add_argument(
        "--operation-id",
        help="reuse one op_<32 lowercase hex> identifier for safe request replay",
    )
    _adaptive_scope_arguments(correct)
    forget = adaptive_actions.add_parser(
        "forget", help="erase the content of one complete memory lineage"
    )
    forget.add_argument("memory_id")
    _adaptive_scope_arguments(forget)
    forget.add_argument(
        "--confirm",
        action="store_true",
        help="confirm irreversible lineage content erasure",
    )
    forget.add_argument(
        "--operation-id",
        help="reuse one op_<32 lowercase hex> identifier for safe request replay",
    )

    integration = areas.add_parser(
        "integration", help="render inert local Agent integration instructions"
    )
    integration_actions = integration.add_subparsers(dest="action", required=True)
    render = integration_actions.add_parser(
        "render", help="render a client setup plan without changing its config"
    )
    render.add_argument(
        "--client", choices=("hermes", "claude", "codex"), required=True
    )
    render.add_argument("--platform", choices=("windows-wsl",), default="windows-wsl")
    render.add_argument("--distro")
    render.add_argument("--name", default="agiwiki")
    render.add_argument("--python-executable")
    render.add_argument(
        "--claude-scope", choices=("local", "project", "user"), default="local"
    )
    skill_path = integration_actions.add_parser(
        "skill-path", help="locate one bundled Agent Skill without installing it"
    )
    skill_path.add_argument(
        "--capability", choices=("read", "author", "review"), required=True
    )

    doctor = areas.add_parser("doctor", help="run layered read-only diagnostics")
    doctor.add_argument(
        "--platform", choices=("auto", "linux", "windows-wsl"), default="auto"
    )
    doctor.add_argument("--distro")
    mcp = areas.add_parser("mcp", help="run the two-tool stdio MCP server")
    mcp.add_argument("--project")
    return parser


def _paths(explicit: str | None):
    if explicit is None:
        return resolve_home_paths()
    return resolve_home_paths({"AGIWIKI_HOME": explicit})


def _adaptive_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--scope-type",
        choices=("user", "agent", "run", "workspace"),
        required=True,
    )
    parser.add_argument("--scope-key", required=True)


def _adaptive_input(path: str) -> dict[str, Any]:
    if path != "-":
        return load_adaptive_input(path)
    payload = sys.stdin.buffer.read(MAX_ADAPTIVE_INPUT_BYTES + 1)
    return parse_adaptive_input(payload)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return value


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
