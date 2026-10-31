"""Tests for the command line interface.

    python3 tests/test_cli.py

Checks the retained convention: the modes that make no model call are mutually
exclusive, while prompt export composes with a normal run.
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run  # noqa: E402


class TestArgumentParsing(unittest.TestCase):
    def test_defaults(self):
        args = run._parse_args([])
        self.assertFalse(args.dry_run)
        self.assertFalse(args.plan_only)
        self.assertFalse(args.show_prompt)
        self.assertFalse(args.prompt_only)
        self.assertFalse(args.export_prompts)
        self.assertFalse(args.no_resume)

    def test_prompt_only(self):
        self.assertTrue(run._parse_args(["--prompt-only"]).prompt_only)

    def test_export_is_composable(self):
        args = run._parse_args(["--dry-run", "--export-prompts"])
        self.assertTrue(args.dry_run)
        self.assertTrue(args.export_prompts)

    def test_modes_are_mutually_exclusive(self):
        conflicts = [
            ("--dry-run", "--plan-only"),
            ("--dry-run", "--show-prompt"),
            ("--dry-run", "--prompt-only"),
            ("--plan-only", "--show-prompt"),
            ("--plan-only", "--prompt-only"),
            ("--show-prompt", "--prompt-only"),
        ]
        for pair in conflicts:
            with self.subTest(pair=pair):
                buffer = io.StringIO()
                with contextlib.redirect_stderr(buffer):
                    with self.assertRaises(SystemExit):
                        run._parse_args(list(pair))
                self.assertIn("not allowed with", buffer.getvalue())


class TestDocumentation(unittest.TestCase):
    """Prevents drift: an option added to the CLI must be documented in the README."""

    def test_every_option_is_in_the_readme(self):
        help_txt = run._build_parser().format_help()
        flags = set(re.findall(r"(--[a-z][a-z-]+)", help_txt)) - {"--help"}
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        missing = sorted(flag for flag in flags if flag not in readme)
        self.assertEqual(missing, [], f"options missing from the README: {missing}")

    def test_help_lists_the_exclusive_modes(self):
        # The usage line is wrapped according to the Python version and terminal
        # width: compare after normalising whitespace.
        help_txt = " ".join(run._build_parser().format_help().split())
        self.assertIn("--dry-run | --plan-only | --show-prompt | --prompt-only", help_txt)


class TestPromptOnly(unittest.TestCase):
    """prompt-only must touch neither the network nor the authentication."""

    def test_writes_prompts_and_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
            cfg["run"]["n_reports"] = 3
            cfg["run"]["run_id"] = "test-prompts"
            cfg["run"]["output_dir"] = str(tmp_path)  # absolute path: outside the project
            settings = tmp_path / "settings.json"
            settings.write_text(json.dumps(cfg), encoding="utf-8")

            code = run.main(["--settings", str(settings), "--prompt-only", "--quiet"])
            self.assertEqual(code, 0)

            prompts_dir = tmp_path / "test-prompts" / "prompts"
            md_files = sorted(prompts_dir.glob("*.md"))
            self.assertEqual(len(md_files), 3)

            content = md_files[0].read_text(encoding="utf-8")
            self.assertIn("CAD-RADS", content)
            self.assertIn("Imposed conclusion", content)

            index = (prompts_dir / "index.tsv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(index), 4)  # header + 3 cases
            self.assertTrue(index[0].startswith("case_id"))

            # no report generated: this mode does not call the model
            reports_dir = tmp_path / "test-prompts" / "reports"
            self.assertEqual(list(reports_dir.glob("*.txt")) if reports_dir.exists() else [], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
