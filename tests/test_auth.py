"""Tests de l'authentification et du garde-fou de connexion.

    python3 tests/test_auth.py

Aucun appel reseau : le test du binaire absent exerce le chemin d'echec.
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
            "command": command or ["binaire-inexistant-xyz", "run", "{prompt}"],
            "model_label": "test/modele",
            "timeout_s": 5,
            "retries": 0,
            "retry_backoff_s": 0,
        }
    }


class TestState(unittest.TestCase):
    def test_aller_retour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(auth.load_state(root))
            auth.save_state(root, {"probe_ok": True, "model_label": "a/b"})
            state = auth.load_state(root)
            self.assertTrue(state["probe_ok"])
            self.assertEqual(state["model_label"], "a/b")

    def test_json_corrompu_retourne_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = auth.state_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{ pas du json", encoding="utf-8")
            self.assertIsNone(auth.load_state(root))

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            auth.save_state(root, {"probe_ok": True})
            auth.clear_state(root)
            self.assertIsNone(auth.load_state(root))
            auth.clear_state(root)  # ne doit pas lever si absent

    def test_state_ne_contient_aucun_secret(self):
        """Le fichier d'etat est un cache : il ne doit jamais porter de cle."""
        state = auth.build_state(fake_cfg(), {"ok": True, "latency_s": 1.2,
                                             "skipped": False, "sample": "PROBE-OK",
                                             "marker_found": True, "error": None})
        serialized = json.dumps(state).lower()
        for forbidden in ("api_key", "apikey", "secret", "token", "password", "bearer"):
            self.assertNotIn(forbidden, serialized)
        self.assertTrue(state["probe_ok"])
        # et il reste serialisable
        json.dumps(state)


class TestProbe(unittest.TestCase):
    def test_dry_run_est_ignore(self):
        result = auth.probe(fake_cfg(backend="dry_run"))
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["latency_s"], 0.0)

    def test_binaire_absent(self):
        result = auth.probe(fake_cfg())
        self.assertFalse(result["ok"])
        self.assertFalse(result["skipped"])
        self.assertIn("inexistant", result["error"])

    def test_available_reflete_le_path(self):
        self.assertFalse(auth.available(fake_cfg()))
        self.assertTrue(auth.available(fake_cfg(command=["python3", "-c", "print(1)"])))

    def test_build_state_sur_echec(self):
        state = auth.build_state(fake_cfg(), {"ok": False, "error": "boum",
                                             "latency_s": 0.0, "skipped": False})
        self.assertFalse(state["probe_ok"])
        self.assertEqual(state["error"], "boum")


if __name__ == "__main__":
    unittest.main(verbosity=2)
