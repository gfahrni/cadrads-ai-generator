"""Tests de la reecriture du modele dans settings.json.

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


def _write_settings(directory: Path, llm: dict) -> Path:
    path = directory / "settings.json"
    path.write_text(json.dumps({"llm": llm, "run": {"n_reports": 3}}), encoding="utf-8")
    return path


class TestSetLlmModel(unittest.TestCase):
    def test_remplace_le_modele_existant(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "--model", "vieux/modele", "{prompt}"],
                "model_label": "vieux/modele@2020-01-01",
            })
            cfg = config.set_llm_model(path, "opencode/bon-modele", stamp="2026-10-31")
            self.assertEqual(cfg["llm"]["command"],
                             ["opencode", "run", "--model", "opencode/bon-modele", "{prompt}"])
            self.assertEqual(cfg["llm"]["model_label"], "opencode/bon-modele@2026-10-31")

    def test_insere_le_flag_model_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "{prompt}"],
            })
            cfg = config.set_llm_model(path, "a/b", stamp="2026-10-31")
            self.assertIn("--model", cfg["llm"]["command"])
            self.assertIn("a/b", cfg["llm"]["command"])
            self.assertIn("{prompt}", cfg["llm"]["command"])

    def test_ajoute_le_placeholder_prompt_si_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "--model", "vieux/modele"],
            })
            cfg = config.set_llm_model(path, "a/b", stamp="2026-10-31")
            self.assertIn("{prompt}", cfg["llm"]["command"])

    def test_ne_touche_aux_autres_reglages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "--model", "vieux/modele", "{prompt}"],
                "timeout_s": 42,
            })
            config.set_llm_model(path, "a/b", stamp="2026-10-31")
            brut = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(brut["llm"]["timeout_s"], 42)
            self.assertEqual(brut["run"]["n_reports"], 3)

    def test_le_resultat_reste_une_config_valide(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_settings(Path(tmp), {
                "backend": "opencode",
                "command": ["opencode", "run", "--model", "vieux/modele", "{prompt}"],
            })
            cfg = config.set_llm_model(path, "a/b", stamp="2026-10-31")
            self.assertEqual(config.validate(cfg), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
