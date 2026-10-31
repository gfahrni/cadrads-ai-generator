"""Tests des decouvertes fortuites mineures.

    python3 tests/test_incidental.py
"""
from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import incidental  # noqa: E402

CATALOGUE_PATH = ROOT / "reference" / "incidental_findings.json"


def make_cfg(**overrides):
    block = {
        "describe_normal": False,
        "include_incidental": True,
        "prevalence_scale": 1.0,
        "max_per_case": 20,
        "exclude": [],
    }
    block.update(overrides)
    return {"other_structures": block, "checks": {}}


class TestCatalogue(unittest.TestCase):
    def setUp(self):
        self.catalogue = incidental.load(CATALOGUE_PATH)

    def test_chargement(self):
        self.assertGreater(len(self.catalogue["findings"]), 5)
        self.assertGreater(len(self.catalogue["organs"]), 3)

    def test_chaque_decouverte_a_un_organe_connu(self):
        for finding in self.catalogue["findings"]:
            self.assertIn(finding["organ"], self.catalogue["organs"])

    def test_prevalences_dans_les_bornes(self):
        for finding in self.catalogue["findings"]:
            self.assertGreaterEqual(finding["prevalence"], 0.0)
            self.assertLessEqual(finding["prevalence"], 1.0)

    def test_ids_uniques(self):
        ids = [f["id"] for f in self.catalogue["findings"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_mots_cles_multilingues(self):
        finding = self.catalogue["findings"][0]
        for language in ("en", "fr", "de", "it"):
            self.assertTrue(
                incidental.keywords_for(finding, finding["organ"], language, self.catalogue),
                f"pas de mots-cles {language} pour {finding['id']}",
            )
        self.assertEqual(incidental.keywords_for(finding, finding["organ"], "es", self.catalogue), [])


class TestTirage(unittest.TestCase):
    def setUp(self):
        self.catalogue = incidental.load(CATALOGUE_PATH)

    def test_desactive_par_defaut(self):
        cfg = make_cfg(include_incidental=False)
        self.assertEqual(incidental.sample(random.Random(1), 70, "female", cfg, self.catalogue), [])

    def test_deterministe(self):
        cfg = make_cfg()
        a = incidental.sample(random.Random(42), 70, "female", cfg, self.catalogue)
        b = incidental.sample(random.Random(42), 70, "female", cfg, self.catalogue)
        self.assertEqual([f["id"] for f in a], [f["id"] for f in b])

    def test_respecte_le_seuil_d_age(self):
        cfg = make_cfg()
        young = incidental.sample(random.Random(7), 40, "female", cfg, self.catalogue)
        for finding in young:
            self.assertEqual(finding.get("age_min", 0), 0, f"{finding['id']} ne devrait pas apparaitre a 40 ans")

    def test_respecte_la_restriction_de_sexe(self):
        cfg = make_cfg()
        rng = random.Random(3)
        for _ in range(300):
            for finding in incidental.sample(rng, 80, "male", cfg, self.catalogue):
                self.assertNotEqual(finding.get("sex"), "female", f"{finding['id']} est reserve aux femmes")

    def test_plafond_par_cas(self):
        cfg = make_cfg(max_per_case=1)
        rng = random.Random(11)
        for _ in range(200):
            self.assertLessEqual(len(incidental.sample(rng, 85, "female", cfg, self.catalogue)), 1)

    def test_exclusion(self):
        cfg = make_cfg(exclude=["aortic_calcification", "vertebral_degenerative"])
        rng = random.Random(5)
        for _ in range(300):
            ids = {f["id"] for f in incidental.sample(rng, 85, "female", cfg, self.catalogue)}
            self.assertNotIn("aortic_calcification", ids)
            self.assertNotIn("vertebral_degenerative", ids)

    def test_scale_zero_desactive(self):
        cfg = make_cfg(prevalence_scale=0.0)
        rng = random.Random(9)
        for _ in range(100):
            self.assertEqual(incidental.sample(rng, 85, "female", cfg, self.catalogue), [])

    def test_prevalences_conformes_au_catalogue(self):
        """A 70 ans, sexe feminin, plafond eleve : la prevalence observee doit
        coller a celle du catalogue (tolerance statistique)."""
        cfg = make_cfg()
        rng = random.Random(2024)
        n = 5000
        counts = {}
        for _ in range(n):
            for finding in incidental.sample(rng, 70, "female", cfg, self.catalogue):
                counts[finding["id"]] = counts.get(finding["id"], 0) + 1

        for finding in self.catalogue["findings"]:
            observed = counts.get(finding["id"], 0) / n
            expected = finding["prevalence"]
            self.assertAlmostEqual(
                observed, expected, delta=0.03,
                msg=f"{finding['id']} : observe {observed:.3f} vs catalogue {expected:.3f}",
            )


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.catalogue = incidental.load(CATALOGUE_PATH)

    def test_identifiant_inconnu_signale(self):
        errors = incidental.config_errors(make_cfg(exclude=["nexiste_pas"]), self.catalogue)
        self.assertTrue(errors)
        self.assertIn("nexiste_pas", errors[0])

    def test_valeurs_negatives_signalees(self):
        self.assertTrue(incidental.config_errors(make_cfg(max_per_case=-1), self.catalogue))
        self.assertTrue(incidental.config_errors(make_cfg(prevalence_scale=-2), self.catalogue))

    def test_config_valide(self):
        self.assertEqual(incidental.config_errors(make_cfg(), self.catalogue), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
