"""Minimum information-completeness checks for factual-memory Entries.

These checks deliberately do not claim to prove factual correctness.  They only
prevent structurally valid but operationally useless one-word memories from
being validated or packed.
"""

from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping, Sequence


ENTRY_QUALITY_POLICY = "agiwiki.entry-quality.v1"


class EntryQualityError(ValueError):
    """An Entry is too information-poor for a reusable Memory Pack."""


def validate_entry_quality(
    entry: Mapping[str, Any],
    *,
    source_path: str | os.PathLike[str] | None = None,
) -> None:
    """Reject obviously underspecified content after structural validation."""

    prefix = "" if source_path is None else f"{Path(source_path)}: "
    entry_id = str(entry["entry_id"])

    def require_text(value: str, minimum: int, pointer: str) -> None:
        if _information_length(value) < minimum:
            raise EntryQualityError(
                f"{prefix}{entry_id} is too brief at {pointer}; "
                f"provide at least {minimum} letters or numbers"
            )

    require_text(str(entry["summary"]), 16, "/summary")
    if len(entry["keywords"]) < 2:
        raise EntryQualityError(
            f"{prefix}{entry_id} needs at least two retrieval keywords"
        )

    content = entry["content"]
    kind = entry["kind"]
    if kind == "fact":
        require_text(str(content["statement"]), 16, "/content/statement")
    elif kind == "concept":
        require_text(str(content["definition"]), 16, "/content/definition")
        supporting = (
            len(content["details"])
            + len(content["examples"])
            + len(content["misconceptions"])
        )
        if supporting < 1:
            raise EntryQualityError(
                f"{prefix}{entry_id} concept needs a detail, example, or misconception"
            )
    elif kind == "procedure":
        require_text(str(content["goal"]), 16, "/content/goal")
        for index, step in enumerate(content["steps"]):
            base = f"/content/steps/{index}"
            require_text(str(step["action"]), 8, f"{base}/action")
            require_text(
                str(step["expected_result"]), 8, f"{base}/expected_result"
            )
            require_text(str(step["verification"]), 8, f"{base}/verification")
        _require_text_list(
            content["verification"],
            8,
            "/content/verification",
            require_text,
        )
    elif kind == "troubleshooting":
        _require_text_list(content["symptoms"], 8, "/content/symptoms", require_text)
        for index, diagnostic in enumerate(content["diagnostic_steps"]):
            base = f"/content/diagnostic_steps/{index}"
            require_text(str(diagnostic["check"]), 8, f"{base}/check")
            require_text(
                str(diagnostic["expected_signal"]), 8, f"{base}/expected_signal"
            )
            for branch_index, branch in enumerate(diagnostic["branches"]):
                branch_base = f"{base}/branches/{branch_index}"
                require_text(str(branch["when"]), 6, f"{branch_base}/when")
                require_text(str(branch["guidance"]), 8, f"{branch_base}/guidance")
        for index, fix in enumerate(content["fixes"]):
            base = f"/content/fixes/{index}"
            require_text(str(fix["action"]), 8, f"{base}/action")
            require_text(str(fix["verification"]), 8, f"{base}/verification")


def validate_entries_quality(
    entries: Sequence[Mapping[str, Any]],
    *,
    source_paths: Mapping[str, Path] | None = None,
) -> None:
    for entry in entries:
        entry_id = str(entry["entry_id"])
        validate_entry_quality(
            entry,
            source_path=None if source_paths is None else source_paths.get(entry_id),
        )


def _information_length(value: str) -> int:
    return sum(1 for character in value if character.isalnum())


def _require_text_list(
    values: Sequence[str],
    minimum: int,
    pointer: str,
    require_text: Callable[[str, int, str], None],
) -> None:
    for index, value in enumerate(values):
        require_text(str(value), minimum, f"{pointer}/{index}")


__all__ = [
    "ENTRY_QUALITY_POLICY",
    "EntryQualityError",
    "validate_entries_quality",
    "validate_entry_quality",
]
