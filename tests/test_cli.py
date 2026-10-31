"""Tests de l'interface en ligne de commande.

    python3 tests/test_cli.py

Verifie la convention retenue : les modes sans appel au modele sont exclusifs
entre eux, l'export de prompts est composable avec un run normal.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import run  # noqa: E402


class TestAnalyseDesArguments(unittest.TestCase):
    def test_defauts(self):
        args = run._parse_args([])
        self.assertFalse(args.dry_run)
        self.assertFalse(args.plan_only)
        self.assertFalse(args.show_prompt)
        self.assertFalse(args.prompt_only)
        self.assertFalse(args.export_prompts)
        self.assertFalse(args.no_resume)

    def test_prompt_only(self):
        self.assertTrue(run._parse_args(["--prompt-only"]).prompt_only)

    def test_export_composable(self):
        args = run._parse_args(["--dry-run", "--export-prompts"])
        self.assertTrue(args.dry_run)
        self.assertTrue(args.export_prompts)

    def test_modes_exclusifs(self):
        conflits = [
            ("--dry-run", "--plan-only"),
            ("--dry-run", "--show-prompt"),
            ("--dry-run", "--prompt-only"),
            ("--plan-only", "--show-prompt"),
            ("--plan-only", "--prompt-only"),
            ("--show-prompt", "--prompt-only"),
        ]
        for paire in conflits:
            with self.subTest(paire=paire):
                buffer = io.StringIO()
                with contextlib.redirect_stderr(buffer):
                    with self.assertRaises(SystemExit):
                        run._parse_args(list(paire))
                self.assertIn("not allowed with", buffer.getvalue())


class TestDocumentation(unittest.TestCase):
    """Empeche la derive : une option ajoutee doit etre documentee dans le README."""

    def test_toutes_les_options_sont_dans_le_readme(self):
        import re

        help_txt = run._build_parser().format_help()
        flags = set(re.findall(r"(--[a-z][a-z-]+)", help_txt)) - {"--help"}
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        manquants = sorted(flag for flag in flags if flag not in readme)
        self.assertEqual(manquants, [], f"options absentes du README : {manquants}")

    def test_l_aide_liste_les_modes_exclusifs(self):
        # La ligne d'usage est repliee selon la version de Python et la largeur
        # du terminal : on compare apres normalisation des espaces.
        aide = " ".join(run._build_parser().format_help().split())
        self.assertIn("--dry-run | --plan-only | --show-prompt | --prompt-only", aide)


class TestPromptOnly(unittest.TestCase):
    """Le mode prompt-only ne doit toucher ni au reseau ni a l'authentification."""

    def test_ecrit_prompts_et_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
            cfg["run"]["n_reports"] = 3
            cfg["run"]["run_id"] = "test-prompts"
            cfg["run"]["output_dir"] = str(tmp_path)  # chemin absolu : hors projet
            settings = tmp_path / "settings.json"
            settings.write_text(json.dumps(cfg), encoding="utf-8")

            code = run.main(["--settings", str(settings), "--prompt-only", "--quiet"])
            self.assertEqual(code, 0)

            prompts_dir = tmp_path / "test-prompts" / "prompts"
            md_files = sorted(prompts_dir.glob("*.md"))
            self.assertEqual(len(md_files), 3)

            contenu = md_files[0].read_text(encoding="utf-8")
            self.assertIn("CAD-RADS", contenu)
            self.assertIn("Imposed conclusion", contenu)

            index = (prompts_dir / "index.tsv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(index), 4)  # en-tete + 3 cas
            self.assertTrue(index[0].startswith("case_id"))

            # aucun rapport genere : ce mode n'appelle pas le modele
            reports_dir = tmp_path / "test-prompts" / "reports"
            self.assertEqual(list(reports_dir.glob("*.txt")) if reports_dir.exists() else [], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
