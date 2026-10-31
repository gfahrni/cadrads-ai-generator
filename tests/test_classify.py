"""Tests for the CAD-RADS 2.0 classification engine.

    python3 tests/test_classify.py

No dependencies. These tests are the guarantee that the label imposed on the LLM
follows the rules: ship them with the repository for reproducibility.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.classify import classify, classify_category  # noqa: E402


def lesion(vessel, pct, segment="mid", plaque="calcified"):
    return {"vessel": vessel, "segment": segment, "stenosis_pct": pct, "plaque": plaque}


class TestCategory(unittest.TestCase):
    def test_no_plaque(self):
        self.assertEqual(classify_category([])[0], "0")

    def test_stenosis_thresholds(self):
        for pct, expected in [(1, "1"), (24, "1"), (25, "2"), (49, "2"),
                              (50, "3"), (69, "3"), (70, "4A"), (99, "4A")]:
            with self.subTest(pct=pct):
                self.assertEqual(classify_category([lesion("LAD", pct)])[0], expected)

    def test_total_occlusion(self):
        self.assertEqual(classify_category([lesion("RCA", 100)])[0], "5")

    def test_left_main(self):
        self.assertEqual(classify_category([lesion("LM", 55), lesion("LAD", 80)])[0], "4B")
        # left main at 49% is not enough
        self.assertEqual(classify_category([lesion("LM", 49), lesion("LAD", 80)])[0], "4A")

    def test_three_vessels(self):
        three = [lesion("LAD", 75), lesion("LCX", 80), lesion("RCA", 70)]
        self.assertEqual(classify_category(three)[0], "4B")
        # only two vessels -> 4A
        self.assertEqual(classify_category(three[:2])[0], "4A")

    def test_two_severe_vessels_stay_4A(self):
        self.assertEqual(classify_category([lesion("LAD", 90), lesion("LCX", 85)])[0], "4A")

    def test_precedence_5_over_4B(self):
        # occlusion plus left main: the retained convention gives 5
        self.assertEqual(classify_category([lesion("LAD", 100), lesion("LM", 60)])[0], "5")

    def test_non_diagnostic(self):
        # no stenosis >= 50% plus a non-diagnostic segment -> N
        self.assertEqual(classify_category([lesion("LAD", 30)], non_evaluable=["RCA mid"])[0], "N")
        self.assertEqual(classify_category([], non_evaluable=["RCA mid"])[0], "N")
        # stenosis >= 50%: the category holds, N becomes a modifier
        result = classify([lesion("LAD", 60)], modifiers=["N"], non_evaluable=["RCA mid"])
        self.assertEqual(result["category"], "3")
        self.assertIn("N", result["modifiers"])
        self.assertEqual(result["label"], "CAD-RADS 3/P1/N")


class TestLabel(unittest.TestCase):
    def test_simple_label(self):
        self.assertEqual(classify([lesion("LAD", 80)], plaque_burden="P2")["label"],
                         "CAD-RADS 4A/P2")

    def test_label_with_modifier(self):
        result = classify([lesion("LAD", 80)], modifiers=["S"], plaque_burden="P3")
        self.assertEqual(result["label"], "CAD-RADS 4A/P3/S")

    def test_modifier_order(self):
        result = classify([lesion("LAD", 60)], modifiers=["S", "G", "E"],
                          non_evaluable=["RCA mid"], plaque_burden="P2")
        self.assertEqual(result["label"], "CAD-RADS 3/P2/N/S/G/E")

    def test_modifiers_dropped_in_cad_rads_0(self):
        result = classify([], modifiers=["S", "V", "G", "E"])
        self.assertEqual(result["category"], "0")
        self.assertEqual(result["modifiers"], ["E"])
        self.assertIsNone(result["plaque_burden"])

    def test_modifier_n_without_non_diagnostic_segment(self):
        result = classify([lesion("LAD", 60)], modifiers=["N"])
        self.assertEqual(result["modifiers"], [])


class TestBurden(unittest.TestCase):
    def test_burden_consistent_with_a_single_lesion(self):
        # P4 (extensive) is impossible with a single diseased vessel
        result = classify([lesion("LAD", 80)], plaque_burden="P4")
        self.assertEqual(result["plaque_burden"], "P4")
        self.assertTrue(any("WARNING" in line for line in result["trace"]))

    def test_burden_follows_extent(self):
        # P measures extent: one vessel -> P1, two -> P2, three -> P3
        self.assertEqual(classify([lesion("LAD", 80)])["plaque_burden"], "P1")
        self.assertEqual(classify([lesion("LAD", 80), lesion("LCX", 60)])["plaque_burden"], "P2")
        trois = [lesion("LAD", 80), lesion("LCX", 60), lesion("RCA", 90)]
        self.assertEqual(classify(trois)["plaque_burden"], "P3")

    def test_burden_is_not_tied_to_the_category(self):
        # the burden follows the plaque amount, not the category
        self.assertEqual(classify([lesion("RCA", 100)])["plaque_burden"], "P2")

    def test_small_plaques_never_get_a_severe_burden(self):
        # three plaques under 50 % score 3: that is P2, not "severe"
        petits = [lesion("LAD", 8), lesion("LCX", 6), lesion("RCA", 20)]
        self.assertEqual(classify(petits)["plaque_burden"], "P2")

    def test_three_occlusions_reach_p4(self):
        occlusions = [lesion("LAD", 100), lesion("LCX", 100), lesion("RCA", 100)]
        self.assertEqual(classify(occlusions)["plaque_burden"], "P4")

    def test_no_burden_when_category_is_n(self):
        result = classify([], non_evaluable=["RCA mid"])
        self.assertEqual(result["category"], "N")
        self.assertIsNone(result["plaque_burden"])
        self.assertEqual(result["label"], "CAD-RADS N")


class TestPlausibleScenarios(unittest.TestCase):
    def test_4b_left_main_with_stent(self):
        lesions = [lesion("LM", 55, "ostial"), lesion("LAD", 87, "proximal")]
        result = classify(lesions, modifiers=["S"], plaque_burden="P3")
        self.assertEqual(result["label"], "CAD-RADS 4B/P3/S")

    def test_diffuse_three_vessel_case(self):
        lesions = [lesion("LAD", 75), lesion("LCX", 80), lesion("RCA", 70)]
        self.assertEqual(classify(lesions, plaque_burden="P4")["label"], "CAD-RADS 4B/P4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
