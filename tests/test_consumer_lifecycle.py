from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from agiwiki.cli import main


ENTRY_ID = "entry_1234567890abcdef1234567890abcdef"


class ConsumerLifecycleTest(unittest.TestCase):
    def test_first_use_authoring_to_active_memory(self) -> None:
        """Exercise only public CLI commands used by a first-time consumer."""

        author_skill = self._run("integration", "skill-path", "--capability", "author")
        self.assertTrue(author_skill["available"])
        self.assertTrue(Path(author_skill["entrypoint"]).is_file())
        self.assertTrue(
            (
                Path(author_skill["path"]) / "references" / "authoring-contract.md"
            ).is_file()
        )

        read_skill = self._run("integration", "skill-path", "--capability", "read")
        self.assertTrue(read_skill["available"])

        with tempfile.TemporaryDirectory(prefix="agiwiki-consumer-") as temporary:
            root = Path(temporary)
            source = root / "manual.md"
            workspace = root / "memory"
            pack = root / "manual.memory-pack"
            home = root / "home"
            source.write_text(
                "第一行给出可复用事实。\n"
                "第二行说明它适用于测试版。\n"
                "第三行包含其他资料。\n"
                "第四行用于结束批次。\n",
                encoding="utf-8",
            )

            initialized = self._run(
                "workspace",
                "init",
                str(workspace),
                "--slug",
                "consumer-manual",
                "--title",
                "Consumer Manual",
                "--locale",
                "zh-CN",
            )
            self.assertTrue(initialized["workspace_id"].startswith("ws_"))

            planned = self._run(
                "author",
                "plan",
                str(source),
                "--workspace",
                str(workspace),
                "--batch-size",
                "2",
            )
            plan_id = planned["plan_id"]
            source_id = planned["source_id"]
            self.assertEqual(planned["batch_count"], 2)

            first = self._run("author", "next", plan_id, "--workspace", str(workspace))
            self.assertEqual(first["locator"]["start"], 1)
            self.assertEqual(first["locator"]["end"], 2)

            entry = {
                "contract_version": "agiwiki.entry.v1",
                "entry_id": ENTRY_ID,
                "kind": "fact",
                "title": "测试版手册中的可复用事实",
                "summary": (
                    "这条事实说明测试版手册前两行包含一个可复用、可定位并可再次核验的结论。"
                ),
                "content": {
                    "statement": (
                        "手册第一行给出可复用事实，第二行把适用范围限定为测试版。"
                    ),
                    "qualifiers": [{"name": "版本", "value": "测试版"}],
                },
                "keywords": ["可复用事实", "测试版手册"],
                "applies_to": ["测试版"],
                "relations": [],
                "source_refs": [
                    {
                        "source_id": source_id,
                        "locator": {"type": "line_range", "value": "1-2"},
                        "support_level": "direct",
                    }
                ],
            }
            (workspace / "entries" / f"{ENTRY_ID}.json").write_text(
                json.dumps(entry, ensure_ascii=False), encoding="utf-8"
            )
            validated = self._run("workspace", "validate", str(workspace))
            self.assertEqual(validated["entry_count"], 1)

            first_result = root / "first-result.json"
            first_result.write_text(
                json.dumps(
                    {
                        **first["result_seed"],
                        "outcome": "completed",
                        "measurement_source": "unavailable",
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "entry_ids": [ENTRY_ID],
                    }
                ),
                encoding="utf-8",
            )
            recorded = self._run(
                "author",
                "record",
                plan_id,
                "--workspace",
                str(workspace),
                "--input",
                str(first_result),
            )
            self.assertEqual(recorded["entry_count"], 1)

            entry_status = self._run(
                "author",
                "entry-status",
                plan_id,
                "--workspace",
                str(workspace),
                "--entry-id",
                ENTRY_ID,
            )
            self.assertEqual(entry_status["binding_state"], "sealed")
            self.assertEqual(
                entry_status["current_entry_digest"],
                entry_status["effective_entry_digest"],
            )

            blocked = self._run(
                "pack",
                "build",
                str(workspace),
                str(pack),
                expected=2,
            )
            self.assertIn("authoring preflight blocked", blocked["message"])
            self.assertFalse(pack.exists())

            second = self._run("author", "next", plan_id, "--workspace", str(workspace))
            second_result = root / "second-result.json"
            second_result.write_text(
                json.dumps(
                    {
                        **second["result_seed"],
                        "outcome": "skipped",
                        "measurement_source": "unavailable",
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "entry_ids": [],
                    }
                ),
                encoding="utf-8",
            )
            self._run(
                "author",
                "record",
                plan_id,
                "--workspace",
                str(workspace),
                "--input",
                str(second_result),
            )
            status = self._run(
                "author", "status", plan_id, "--workspace", str(workspace)
            )
            self.assertEqual(status["progress_basis_points"], 10_000)
            self.assertTrue(status["recorded_entries_ok"])

            built = self._run("pack", "build", str(workspace), str(pack))
            self.assertTrue(built["authoring_preflight"]["ready"])
            self.assertEqual(
                built["authoring_preflight"]["semantic_review"], "NOT_CHECKED"
            )
            self.assertFalse(built["incomplete_authoring_override"])
            verified = self._run("pack", "verify", str(pack))
            self.assertEqual(verified["pack_id"], built["pack_id"])

            self._run("--home", str(home), "home", "init")
            installed = self._run("--home", str(home), "home", "install", str(pack))
            self._run("--home", str(home), "home", "activate", installed["pack_id"])
            found = self._run("--home", str(home), "memory", "find", "测试版可复用事实")
            self.assertTrue(found["found"])
            self.assertEqual(found["results"][0]["entry_id"], ENTRY_ID)
            exact = self._run(
                "--home",
                str(home),
                "memory",
                "get",
                ENTRY_ID,
                "--pack-id",
                installed["pack_id"],
            )
            self.assertTrue(exact["found"])
            self.assertEqual(exact["entry"]["entry_id"], ENTRY_ID)
            self.assertEqual(exact["sources"][0]["source_id"], source_id)

    def _run(self, *arguments: str, expected: int = 0) -> dict:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            return_code = main(list(arguments))
        self.assertEqual(return_code, expected, stderr.getvalue())
        stream = stderr if expected else stdout
        return json.loads(stream.getvalue())


if __name__ == "__main__":
    unittest.main()
