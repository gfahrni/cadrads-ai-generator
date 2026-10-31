"""Every category tree must produce, by construction, the category it targets.

    python3 tests/test_scenarios.py
"""
from __future__ import annotations

import json
import random
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import classify, scenarios  # noqa: E402

TARGETS = ("0", "1", "2", "3", "4A", "4B", "5")
DRAWS = 200


class TestConformance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = json.loads((ROOT / "reference" / "cadrads_2_0.json").read_text(encoding="utf-8"))
        cls.cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))

    def test_every_target_is_produced_by_its_own_tree(self):
        rng = random.Random(11)
        for target in TARGETS:
            with self.subTest(target=target):
                seen = Counter()
                for _ in range(DRAWS):
                    scenario = scenarios.build(rng, self.reference, target, self.cfg)
                    category, _ = classify.classify_category(scenario["lesions"], [])
                    seen[category] += 1
                self.assertEqual(set(seen), {target}, f"target {target} produced {dict(seen)}")

    def test_unknown_target_is_rejected(self):
        with self.assertRaises(ValueError):
            scenarios.build(random.Random(1), self.reference, "6", self.cfg)


class TestTreeRules(unittest.TestCase):
    """The per-category trees must never break their own compatibility rules."""

    @classmethod
    def setUpClass(cls):
        cls.reference = json.loads((ROOT / "reference" / "cadrads_2_0.json").read_text(encoding="utf-8"))
        cls.cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        cls.rng = random.Random(3)
        cls.scenarios = {t: [scenarios.build(cls.rng, cls.reference, t, cls.cfg) for _ in range(DRAWS)]
                         for t in TARGETS}

    def test_cad_rads_0_has_no_lesion(self):
        for scenario in self.scenarios["0"]:
            self.assertEqual(scenario["lesions"], [])
            self.assertTrue(set(scenario["modifiers"]) <= {"E"})

    def test_categories_1_2_3_never_use_the_left_main(self):
        for target in ("1", "2", "3"):
            for scenario in self.scenarios[target]:
                self.assertNotIn("LM", {l["vessel"] for l in scenario["lesions"]},
                                 f"left main in a CAD-RADS {target} scenario")

    def test_4a_never_has_a_left_main_or_three_vessels(self):
        for scenario in self.scenarios["4A"]:
            vessels = {l["vessel"] for l in scenario["lesions"]}
            self.assertNotIn("LM", vessels)
            severe = {l["vessel"] for l in scenario["lesions"] if l["stenosis_pct"] >= 70}
            self.assertLessEqual(len(severe), 2)

    def test_4b_always_has_left_main_or_three_vessels(self):
        for scenario in self.scenarios["4B"]:
            left_main = any(l["vessel"] == "LM" and l["stenosis_pct"] >= 50
                            for l in scenario["lesions"])
            severe = len({l["vessel"] for l in scenario["lesions"] if l["stenosis_pct"] >= 70})
            self.assertTrue(left_main or severe >= 3, f"4B without left main or 3 vessels: {scenario['lesions']}")

    def test_5_always_has_an_occlusion_and_never_v(self):
        for scenario in self.scenarios["5"]:
            self.assertTrue(any(l["stenosis_pct"] >= 100 for l in scenario["lesions"]))
            self.assertNotIn("V", scenario["modifiers"])

    def test_no_graft_modifier_below_a_surgical_stenosis(self):
        for target in ("0", "1", "2", "3"):
            for scenario in self.scenarios[target]:
                self.assertNotIn("G", scenario["modifiers"])

    def test_no_non_diagnostic_modifier_below_50_percent(self):
        # N would reclassify the study, so it must not appear where no stenosis >= 50 %
        for target in ("0", "1", "2"):
            for scenario in self.scenarios[target]:
                self.assertNotIn("N", scenario["modifiers"])

    def test_modifiers_are_drawn_from_the_allowed_set(self):
        allowed = {"0": {"E"}, "1": {"S", "V", "E"}, "2": {"S", "V", "E"},
                   "3": {"S", "N", "V", "E"}, "4A": {"S", "G", "N", "V", "E"},
                   "4B": {"S", "G", "N", "V", "E"}, "5": {"S", "G", "N", "E"}}
        for target, scenario_list in self.scenarios.items():
            for scenario in scenario_list:
                self.assertTrue(set(scenario["modifiers"]) <= allowed[target],
                                f"unexpected modifier in {target}: {scenario['modifiers']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
