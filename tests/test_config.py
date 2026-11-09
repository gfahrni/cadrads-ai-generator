"""Tests for rewriting the model in settings.json.

    python3 tests/test_config.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _write_settings(directory: Path, llm: dict) -> Path:
    path = directory / "settings.json"
    path.write_text(json.dumps({"llm": llm, "run": {"n_reports": 3}}), encoding="utf-8")
    return path


class TestIndication(unittest.TestCase):
    def _cfg(self, indication):
        cfg = config.load_settings(ROOT / "settings.json")
        cfg["generation"]["indication"] = indication
        return cfg

    def test_random_is_accepted(self):
        # the planner draws stable/acute per case; the validator must allow it
        self.assertEqual(config.validate(self._cfg("random")), [])

    def test_unknown_value_is_rejected(self):
        errors = config.validate(self._cfg("elective"))
        self.assertTrue(any("indication" in e for e in errors))


class TestSetLlmModel(unittest.TestCase):
    def test_replaces_the_existing_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "--model", "old/model", "{prompt}"],
                "model_label": "old/model@2020-01-01",
            })
            cfg = config.set_llm_model(path, "opencode/good-model", stamp="2026-10-31")
            self.assertEqual(cfg["llm"]["command"],
                             ["opencode", "run", "--model", "opencode/good-model", "{prompt}"])
            self.assertEqual(cfg["llm"]["model_label"], "opencode/good-model@2026-10-31")

    def test_inserts_the_model_flag_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "{prompt}"],
            })
            cfg = config.set_llm_model(path, "a/b", stamp="2026-10-31")
            self.assertIn("--model", cfg["llm"]["command"])
            self.assertIn("a/b", cfg["llm"]["command"])
            self.assertIn("{prompt}", cfg["llm"]["command"])

    def test_appends_the_prompt_placeholder_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "--model", "old/model"],
            })
            cfg = config.set_llm_model(path, "a/b", stamp="2026-10-31")
            self.assertIn("{prompt}", cfg["llm"]["command"])

    def test_leaves_other_settings_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "--model", "old/model", "{prompt}"],
                "timeout_s": 42,
            })
            config.set_llm_model(path, "a/b", stamp="2026-10-31")
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["llm"]["timeout_s"], 42)
            self.assertEqual(raw["run"]["n_reports"], 3)

    def test_uses_today_when_no_stamp_is_given(self):
        # regression: datetime was only imported locally, so the default path raised
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "--model", "old/model", "{prompt}"],
            })
            cfg = config.set_llm_model(path, "a/b")
            self.assertTrue(cfg["llm"]["model_label"].startswith("a/b@"))
            self.assertRegex(cfg["llm"]["model_label"], r"@\d{4}-\d{2}-\d{2}$")

    def test_the_result_is_still_a_valid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "--model", "old/model", "{prompt}"],
            })
            cfg = config.set_llm_model(path, "a/b", stamp="2026-10-31")
            self.assertEqual(config.validate(cfg), [])


class TestChecksPolicies(unittest.TestCase):
    def test_unplanned_incidental_policy_is_validated(self):
        cfg = config.load_settings(ROOT / "settings.json")
        self.assertIn("unplanned_incidental", cfg["checks"])
        cfg["checks"]["unplanned_incidental"] = "sometimes"
        errors = config.validate(cfg)
        self.assertTrue(any("unplanned_incidental" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
