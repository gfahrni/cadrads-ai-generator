"""Tests for authentication and the connection guard.

    python3 tests/test_auth.py

No network call: the missing-binary test exercises the failure path.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import auth  # noqa: E402


def fake_cfg(backend="opencode", command=None):
    return {
        "llm": {
            "backend": backend,
            "command": command or ["binary-that-does-not-exist-xyz", "run", "{prompt}"],
            "model_label": "test/model",
            "timeout_s": 5,
            "retries": 0,
            "retry_backoff_s": 0,
        }
    }


class TestState(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(auth.load_state(root))
            auth.save_state(root, {"probe_ok": True, "model_label": "a/b"})
            state = auth.load_state(root)
            self.assertTrue(state["probe_ok"])
            self.assertEqual(state["model_label"], "a/b")

    def test_broken_json_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = auth.state_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ not json", encoding="utf-8")
            self.assertIsNone(auth.load_state(root))

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth.save_state(root, {"probe_ok": True})
            auth.clear_state(root)
            self.assertIsNone(auth.load_state(root))
            auth.clear_state(root)  # must not raise when absent

    def test_state_holds_no_secret(self):
        """The state file is a cache: it must never carry a key."""
        state = auth.build_state(fake_cfg(), {"ok": True, "latency_s": 1.2,
                                             "skipped": False, "sample": "PROBE-OK",
                                             "marker_found": True, "error": None})
        serialized = json.dumps(state).lower()
        for forbidden in ("api_key", "apikey", "secret", "token", "password", "bearer"):
            self.assertNotIn(forbidden, serialized)
        self.assertTrue(state["probe_ok"])
        json.dumps(state)


class TestPermanentErrors(unittest.TestCase):
    """A wrong model id must not be retried: retrying wastes 24 s per failure."""

    def test_no_retry_on_model_unavailable(self):
        import time

        from src import llm_client

        cfg = {
            "llm": {
                "backend": "opencode",
                "command": ["sh", "-c", "echo 'Error: Model unavailable: foo' >&2; exit 1"],
                "model_label": "x",
                "timeout_s": 5,
                "retries": 2,
                "retry_backoff_s": 8,
            }
        }
        client = llm_client.get_client(cfg)
        started = time.time()
        result = client.generate("hello", {})
        elapsed = time.time() - started

        self.assertFalse(result.ok)
        self.assertIn("Model unavailable", result.error)
        self.assertEqual(result.attempts, 1)          # no retry
        self.assertLess(elapsed, 5)                    # no 8 s + 16 s backoff

    def test_transient_error_is_retried(self):
        from src import llm_client

        cfg = {
            "llm": {
                "backend": "opencode",
                "command": ["sh", "-c", "exit 1"],
                "model_label": "x",
                "timeout_s": 5,
                "retries": 2,
                "retry_backoff_s": 0,
            }
        }
        result = llm_client.get_client(cfg).generate("hello", {})
        self.assertFalse(result.ok)
        self.assertEqual(result.attempts, 3)          # 1 + 2 retries


class TestProbe(unittest.TestCase):
    def test_dry_run_is_skipped(self):
        result = auth.probe(fake_cfg(backend="dry_run"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["latency_s"], 0.0)

    def test_missing_binary(self):
        result = auth.probe(fake_cfg())
        self.assertFalse(result["ok"])
        self.assertFalse(result["skipped"])
        self.assertIn("does-not-exist", result["error"])

    def test_available_follows_the_path(self):
        self.assertFalse(auth.available(fake_cfg()))
        self.assertTrue(auth.available(fake_cfg(command=["python3", "-c", "print(1)"])))

    def test_build_state_on_failure(self):
        state = auth.build_state(fake_cfg(), {"ok": False, "error": "boom",
                                             "latency_s": 0.0, "skipped": False})
        self.assertFalse(state["probe_ok"])
        self.assertEqual(state["error"], "boom")


if __name__ == "__main__":
    unittest.main(verbosity=2)
