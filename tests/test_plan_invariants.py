"""Invariants of the generated plan: "X cannot happen when Y".

    python3 tests/test_plan_invariants.py

These are the rules the planner must satisfy by construction. They are checked over
a full-sized plan (2,000 cases) rather than on crafted examples, so any future
change to the generation order that reintroduces a contradiction fails here.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import classify, planner, prompt  # noqa: E402

N_CASES = 2000


class PlanInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        cfg["run"]["n_reports"] = N_CASES
        # The invariants must not depend on the local settings: pin the modes so a
        # user-modified settings.json cannot make the suite pass or fail differently.
        cfg["generation"]["cadrads_distribution"] = "auto"
        cfg["generation"]["lesion_mode"] = "multi"
        cls.cfg = cfg
        cls.reference = json.loads((ROOT / "reference" / "cadrads_2_0.json").read_text(encoding="utf-8"))
        cls.catalogue = json.loads((ROOT / "reference" / "incidental_findings.json").read_text(encoding="utf-8"))
        cls.cases = planner.build_cases(cfg, cls.reference, cls.catalogue)

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _lesioned_segments(case):
        return {f"{l['vessel']} {l['segment']}" for l in case["lesions"]}

    # -- stents ------------------------------------------------------------
    def test_no_stent_on_a_stenosed_segment(self):
        """A "patent" stent cannot sit on a segment carrying a >= 50 % stenosis."""
        for case in self.cases:
            if not case["stent_site"]:
                continue
            for lesion in case["lesions"]:
                same = f"{lesion['vessel']} {lesion['segment']}" == case["stent_site"]
                self.assertFalse(same and lesion["stenosis_pct"] >= 50,
                                 f"{case['case_id']}: patent stent on a {lesion['stenosis_pct']}% segment")

    def test_no_stent_on_any_lesioned_segment(self):
        for case in self.cases:
            if case["stent_site"]:
                self.assertNotIn(case["stent_site"], self._lesioned_segments(case),
                                 f"{case['case_id']}: stent shares a segment with a lesion")

    def test_stent_is_always_placed(self):
        for case in self.cases:
            if "S" in case["modifiers"]:
                self.assertTrue(case["stent_site"], f"{case['case_id']}: S without a site")

    # -- calcium -----------------------------------------------------------
    def test_no_calcium_without_calcified_plaque(self):
        """Agatston > 0 requires a calcified or mixed plaque to explain it."""
        for case in self.cases:
            calcified = any(l["plaque"] in ("calcified", "mixed") for l in case["lesions"])
            if not calcified:
                self.assertEqual(case["agatston"], 0,
                                 f"{case['case_id']}: Agatston {case['agatston']} with no calcified plaque")

    def test_calcium_present_when_plaque_is_calcified(self):
        for case in self.cases:
            if any(l["plaque"] == "calcified" for l in case["lesions"]):
                self.assertGreater(case["agatston"], 0, f"{case['case_id']}: calcified plaque, no calcium")

    # -- modifiers ---------------------------------------------------------
    def test_vulnerable_plaque_implies_soft_plaque(self):
        for case in self.cases:
            if "V" in case["modifiers"]:
                self.assertTrue(any(l["plaque"] == "non-calcified" for l in case["lesions"]),
                                f"{case['case_id']}: V on a fully calcified scenario")

    def test_graft_targets_a_surgical_stenosis(self):
        """A bypass is indicated for >= 70 % (>= 50 % for the left main)."""
        for case in self.cases:
            if not case["graft_site"]:
                continue
            candidates = [l for l in case["lesions"]
                          if l["stenosis_pct"] >= (50 if l["vessel"] == "LM" else 70)]
            self.assertTrue(candidates, f"{case['case_id']}: graft with no surgical stenosis to bypass")

    def test_total_occlusion_is_calcified(self):
        """A chronic total occlusion is not a purely soft plaque."""
        for case in self.cases:
            for lesion in case["lesions"]:
                if lesion["stenosis_pct"] >= 100:
                    self.assertIn(lesion["plaque"], ("calcified", "mixed"),
                                  f"{case['case_id']}: non-calcified total occlusion")

    def test_vulnerable_plaque_never_on_an_occlusion(self):
        for case in self.cases:
            if case["vulnerable_site"]:
                occluded = {f"{l['vessel']} {l['segment']}" for l in case["lesions"]
                            if l["stenosis_pct"] >= 100}
                self.assertNotIn(case["vulnerable_site"], occluded,
                                 f"{case['case_id']}: V on a total occlusion")

    def test_no_graft_without_a_significant_lesion(self):
        for case in self.cases:
            if "G" in case["modifiers"]:
                self.assertTrue(case["graft_site"], f"{case['case_id']}: G declared but no graft placed")

    def test_vulnerable_plaque_designates_a_soft_lesion(self):
        for case in self.cases:
            if "V" not in case["modifiers"]:
                self.assertIsNone(case["vulnerable_site"], f"{case['case_id']}: V site without the modifier")
                continue
            self.assertTrue(case["vulnerable_site"], f"{case['case_id']}: V without a designated site")
            soft = {f"{l['vessel']} {l['segment']}" for l in case["lesions"] if l["plaque"] == "non-calcified"}
            self.assertIn(case["vulnerable_site"], soft, f"{case['case_id']}: V site is not a soft plaque")

    def test_exception_names_a_concrete_abnormality(self):
        for case in self.cases:
            if "E" in case["modifiers"]:
                self.assertTrue(case["exception_site"], f"{case['case_id']}: E without an abnormality")
            else:
                self.assertIsNone(case["exception_site"], f"{case['case_id']}: abnormality without E")

    def test_no_stent_graft_or_vulnerable_plaque_in_cad_rads_0(self):
        for case in self.cases:
            if case["cadrads"] == "0":
                for modifier in ("S", "G", "V", "N"):
                    self.assertNotIn(modifier, case["modifiers"],
                                     f"{case['case_id']}: {modifier} in a CAD-RADS 0 case")

    def test_non_diagnostic_segment_is_not_on_a_severe_vessel(self):
        for case in self.cases:
            severe = {l["vessel"] for l in case["lesions"] if l["stenosis_pct"] >= 50}
            for entry in case["non_evaluable"]:
                self.assertNotIn(entry.split()[0], severe,
                                 f"{case['case_id']}: useless non-diagnostic segment on a severe vessel")

    # -- plaque burden -----------------------------------------------------
    def test_no_artefactual_stenosis(self):
        """A stenosis below 5 % is a measurement artefact, not a finding."""
        for case in self.cases:
            for lesion in case["lesions"]:
                self.assertGreaterEqual(lesion["stenosis_pct"], 5,
                                        f"{case['case_id']}: {lesion['stenosis_pct']}% stenosis")

    def test_graft_names_its_anastomosis(self):
        """The model must not have to invent where the graft lands."""
        for case in self.cases:
            if not case["graft_site"]:
                continue
            self.assertTrue(any(v in case["graft_site"] for v in ("LAD", "LCX", "RCA", "left main")),
                            f"{case['case_id']}: graft without an anastomotic target: {case['graft_site']}")
            self.assertIn(" to the ", case["graft_site"],
                          f"{case['case_id']}: graft does not state where it goes")

    def test_calcium_grows_with_age(self):
        """Plaque composition is age-dependent: the score must follow."""
        young = [c["agatston"] for c in self.cases if c["age"] < 55]
        old = [c["agatston"] for c in self.cases if c["age"] >= 75]
        self.assertGreater(sum(old) / len(old), 1.5 * sum(young) / len(young),
                           "the calcium score does not follow the patient age")

    def test_no_two_lesions_in_the_same_artery(self):
        for case in self.cases:
            vessels = [l["vessel"] for l in case["lesions"]]
            self.assertEqual(len(vessels), len(set(vessels)),
                             f"{case['case_id']}: two lesions in the same artery {vessels}")

    def test_burden_is_a_function_of_extent(self):
        for case in self.cases:
            self.assertEqual(case["plaque_burden"], classify.plaque_burden_for(case["lesions"]),
                             f"{case['case_id']}: P does not follow the extent")

    def test_no_high_burden_on_a_single_vessel(self):
        for case in self.cases:
            if len({l["vessel"] for l in case["lesions"]}) == 1:
                self.assertIn(case["plaque_burden"], (None, "P1"),
                              f"{case['case_id']}: {case['plaque_burden']} on one vessel")

    # -- label -------------------------------------------------------------
    def test_every_lesion_stays_at_or_below_the_final_category(self):
        """The patient category is the highest lesion, so no lesion may exceed it."""
        for case in self.cases:
            if not case["per_lesion"]:
                self.assertEqual(case["cadrads"], "0")
                continue
            label = case["cadrads"]
            final = 4 if label in ("4A", "4B") else classify.LESION_ORDER[label]
            for lesion in case["per_lesion"]:
                self.assertLessEqual(classify.LESION_ORDER[lesion["cadrads"]], final,
                                     f"{case['case_id']}: lesion above the final category")

    def test_4a_has_no_left_main_and_no_three_vessel_disease(self):
        for case in self.cases:
            if case["cadrads"] != "4A":
                continue
            self.assertFalse(any(l["vessel"] == "LM" and l["stenosis_pct"] >= 50 for l in case["lesions"]),
                             f"{case['case_id']}: 4A with a significant left main")
            severe = {l["vessel"] for l in case["lesions"] if l["stenosis_pct"] >= 70}
            self.assertLessEqual(len(severe), 2, f"{case['case_id']}: 4A with three vessels >= 70%")

    def test_4b_has_left_main_or_three_vessels(self):
        for case in self.cases:
            if case["cadrads"] != "4B":
                continue
            left_main = any(l["vessel"] == "LM" and l["stenosis_pct"] >= 50 for l in case["lesions"])
            three = len({l["vessel"] for l in case["lesions"] if l["stenosis_pct"] >= 70}) >= 3
            self.assertTrue(left_main or three, f"{case['case_id']}: 4B without left main or 3 vessels")

    def test_5_has_a_total_occlusion(self):
        for case in self.cases:
            if case["cadrads"] == "5":
                self.assertTrue(any(l["stenosis_pct"] >= 100 for l in case["lesions"]),
                                f"{case['case_id']}: 5 without an occlusion")

    def test_label_recomputes_from_the_lesions(self):
        for case in self.cases:
            again = classify.classify(case["lesions"], case["declared_modifiers"],
                                      case["non_evaluable"], case["plaque_burden"])
            self.assertEqual(again["label"], case["label"], f"{case['case_id']}: label not reproducible")

    def test_no_plan_drift(self):
        drift = [c["case_id"] for c in self.cases if not c["target_ok"]]
        self.assertEqual(drift, [], f"target/computed drift: {drift[:5]}")

    def test_distribution_matches_the_weights(self):
        cfg = self.cfg
        total = sum(cfg["generation"]["cadrads_weights"].values())
        for category, weight in cfg["generation"]["cadrads_weights"].items():
            expected = N_CASES * weight / total
            got = sum(1 for c in self.cases if c["cadrads"] == category)
            self.assertLessEqual(abs(got - expected), 1, f"{category}: {got} vs {expected:.0f} expected")

    def test_uniform_distribution_gives_equal_shares(self):
        cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        categories = list(cfg["generation"]["cadrads_weights"])
        cfg["run"]["n_reports"] = 100 * len(categories)
        cfg["generation"]["cadrads_distribution"] = "uniform"
        cases = planner.build_cases(cfg, self.reference, self.catalogue)
        counts = {c: sum(1 for x in cases if x["cadrads"] == c) for c in categories}
        self.assertEqual(set(counts.values()), {100}, f"uniform share broken: {counts}")

    def test_manual_percentages_are_normalised(self):
        cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        cfg["run"]["n_reports"] = 1000
        cfg["generation"]["cadrads_distribution"] = "manual"
        cfg["generation"]["cadrads_weights"] = {"0": 10, "1": 10, "2": 10, "3": 10,
                                                "4A": 10, "4B": 10, "5": 40}
        cases = planner.build_cases(cfg, self.reference, self.catalogue)
        self.assertEqual(sum(1 for x in cases if x["cadrads"] == "5"), 400)
        self.assertEqual(sum(1 for x in cases if x["cadrads"] == "0"), 100)
        self.assertEqual(len(cases), 1000)

    def test_mono_mode_gives_one_lesion_per_patient(self):
        cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        cfg["run"]["n_reports"] = 400
        cfg["generation"]["lesion_mode"] = "mono"
        cases = planner.build_cases(cfg, self.reference, self.catalogue)
        for case in cases:
            expected = 0 if case["cadrads"] == "0" else 1
            self.assertEqual(len(case["lesions"]), expected, f"{case['case_id']}: {case['cadrads']}")


class PromptInvariants(unittest.TestCase):
    """The rendered prompt must not contradict itself."""

    @classmethod
    def setUpClass(cls):
        cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        cfg["run"]["n_reports"] = 60
        cls.cfg = cfg
        cls.reference = json.loads((ROOT / "reference" / "cadrads_2_0.json").read_text(encoding="utf-8"))
        cls.catalogue = json.loads((ROOT / "reference" / "incidental_findings.json").read_text(encoding="utf-8"))
        cls.templates = prompt.load_templates(ROOT / "prompts")
        cls.cases = planner.build_cases(cfg, cls.reference, cls.catalogue)

    def _render(self, case):
        return prompt.render(self.templates, case, self.cfg, self.reference)["prompt"]

    def test_no_unresolved_placeholder(self):
        for case in self.cases:
            self.assertNotIn("{{", self._render(case), f"{case['case_id']}: unresolved placeholder")

    def test_no_duplicated_instruction_line(self):
        for case in self.cases:
            text = self._render(case)
            lines = [l.strip() for l in text.splitlines() if l.strip().startswith("- ")]
            self.assertEqual(len(lines), len(set(lines)), f"{case['case_id']}: duplicated instruction bullet")

    def test_v_and_e_are_rendered_in_the_scenario(self):
        for case in self.cases:
            text = self._render(case)
            if "V" in case["modifiers"]:
                self.assertIn("High-risk plaque", text, f"{case['case_id']}: V not described")
            if "E" in case["modifiers"]:
                self.assertIn("Non-atherosclerotic abnormality", text, f"{case['case_id']}: E not described")

    def test_cad_rads_0_never_asked_for_a_vessel_by_vessel_description(self):
        for case in self.cases:
            if not case["lesions"]:
                text = self._render(case)
                self.assertNotIn("vessel by vessel", text, f"{case['case_id']}: vessel-by-vessel on an empty scenario")
                self.assertIn("no coronary plaque", text, f"{case['case_id']}: no explicit normal statement")

    def test_free_style_does_not_contradict_the_style_rule(self):
        for case in self.cases:
            if case["style"] == "free":
                text = self._render(case)
                self.assertIn("without section headings", text)
                self.assertNotIn("including the headings", text, f"{case['case_id']}: headings contradiction")

    def test_acquisition_is_given(self):
        for case in self.cases:
            self.assertIn("Acquisition:", self._render(case), f"{case['case_id']}: no acquisition context")

    def test_imposed_label_is_in_the_prompt(self):
        for case in self.cases:
            self.assertIn(case["label"], self._render(case), f"{case['case_id']}: label missing from prompt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
