"""Tests for the minor incidental findings.

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

    def test_loading(self):
        self.assertGreater(len(self.catalogue["findings"]), 5)
        self.assertGreater(len(self.catalogue["organs"]), 3)

    def test_every_finding_has_a_known_organ(self):
        for finding in self.catalogue["findings"]:
            self.assertIn(finding["organ"], self.catalogue["organs"])

    def test_prevalences_within_bounds(self):
        for finding in self.catalogue["findings"]:
            self.assertGreaterEqual(finding["prevalence"], 0.0)
            self.assertLessEqual(finding["prevalence"], 1.0)

    def test_ids_are_unique(self):
        ids = [f["id"] for f in self.catalogue["findings"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_multilingual_keywords(self):
        finding = self.catalogue["findings"][0]
        for language in ("en", "fr", "de", "it"):
            self.assertTrue(
                incidental.keywords_for(finding, finding["organ"], language, self.catalogue),
                f"no {language} keywords for {finding['id']}",
            )
        self.assertEqual(incidental.keywords_for(finding, finding["organ"], "es", self.catalogue), [])


class TestSampling(unittest.TestCase):
    def setUp(self):
        self.catalogue = incidental.load(CATALOGUE_PATH)

    def test_disabled_by_default(self):
        cfg = make_cfg(include_incidental=False)
        self.assertEqual(incidental.sample(random.Random(1), 70, "female", cfg, self.catalogue), [])

    def test_deterministic(self):
        cfg = make_cfg()
        a = incidental.sample(random.Random(42), 70, "female", cfg, self.catalogue)
        b = incidental.sample(random.Random(42), 70, "female", cfg, self.catalogue)
        self.assertEqual([f["id"] for f in a], [f["id"] for f in b])

    def test_respects_age_threshold(self):
        cfg = make_cfg()
        young = incidental.sample(random.Random(7), 40, "female", cfg, self.catalogue)
        for finding in young:
            self.assertEqual(finding.get("age_min", 0), 0, f"{finding['id']} should not appear at age 40")

    def test_respects_sex_restriction(self):
        cfg = make_cfg()
        rng = random.Random(3)
        for _ in range(300):
            for finding in incidental.sample(rng, 80, "male", cfg, self.catalogue):
                self.assertNotEqual(finding.get("sex"), "female", f"{finding['id']} is female-only")

    def test_cap_per_case(self):
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

    def test_scale_zero_disables(self):
        cfg = make_cfg(prevalence_scale=0.0)
        rng = random.Random(9)
        for _ in range(100):
            self.assertEqual(incidental.sample(rng, 85, "female", cfg, self.catalogue), [])

    def test_prevalences_match_the_catalogue(self):
        """At age 70, female sex, high cap: the observed prevalence must track the
        catalogue (statistical tolerance)."""
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
                msg=f"{finding['id']}: observed {observed:.3f} vs catalogue {expected:.3f}",
            )


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.catalogue = incidental.load(CATALOGUE_PATH)

    def test_unknown_identifier_is_reported(self):
        errors = incidental.config_errors(make_cfg(exclude=["does_not_exist"]), self.catalogue)
        self.assertTrue(errors)
        self.assertIn("does_not_exist", errors[0])

    def test_negative_values_are_reported(self):
        self.assertTrue(incidental.config_errors(make_cfg(max_per_case=-1), self.catalogue))
        self.assertTrue(incidental.config_errors(make_cfg(prevalence_scale=-2), self.catalogue))

    def test_valid_config(self):
        self.assertEqual(incidental.config_errors(make_cfg(), self.catalogue), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
