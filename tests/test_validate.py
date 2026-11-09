"""Tests for response cleaning and the automatic checks.

    python3 tests/test_validate.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import incidental, validate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

CFG = {
    "checks": {
        "label_integrity": "fail",
        "language_match": "warn",
        "modifier_mention": "warn",
        "incidental_mention": "warn",
        "unplanned_incidental": "warn",
        "min_words": 5,
        "max_words": 900,
        "forbidden_phrases": ["as an ai", "```"],
    }
}


def case(label="CAD-RADS 4A/P2", modifiers=(), incidental=(), language="en"):
    return {
        "label": label,
        "cadrads": label.replace("CAD-RADS ", "").split("/")[0],
        "language": language,
        "modifiers": list(modifiers),
        "incidental": list(incidental),
    }


class TestClean(unittest.TestCase):
    def test_strips_code_fences(self):
        self.assertNotIn("```", validate.clean("```\nFindings: x\nCAD-RADS 4A/P2\n```"))

    def test_strips_preamble(self):
        self.assertTrue(validate.clean("Here is the report:\n\nFindings: x").startswith("Findings"))

    def test_empty_text(self):
        self.assertEqual(validate.clean(""), "")


class TestLabel(unittest.TestCase):
    def test_verbatim_label(self):
        self.assertTrue(validate.label_present("... CAD-RADS 4A/P2", "CAD-RADS 4A/P2"))

    def test_label_with_spaces_and_case(self):
        self.assertTrue(validate.label_present("cad-radas 4a / p2".replace("radas", "rads"), "CAD-RADS 4A/P2"))

    def test_absent_label(self):
        self.assertFalse(validate.label_present("CAD-RADS 2/P2", "CAD-RADS 4A/P2"))

    def test_label_without_burden(self):
        self.assertTrue(validate.label_present("CAD-RADS 0", "CAD-RADS 0"))


class TestCheck(unittest.TestCase):
    def test_compliant_report(self):
        text = "Findings: the LAD shows 80% stenosis. CAD-RADS 4A/P2"
        result = validate.check(text, case(), CFG)
        self.assertTrue(result["ok"])
        self.assertTrue(result["results"]["label"]["ok"])

    def test_wrong_label_excludes(self):
        text = "Findings: the LAD shows 80% stenosis. CAD-RADS 1/P2"
        result = validate.check(text, case(), CFG)
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"]["label"]["status"], "fail")

    def test_stent_not_described(self):
        text = "Findings: the LAD shows 60% stenosis. CAD-RADS 3/P2/S"
        result = validate.check(text, case(label="CAD-RADS 3/P2/S", modifiers=["S"]), CFG)
        self.assertFalse(result["results"]["modifiers"]["ok"])
        # warn: the report is still accepted
        self.assertTrue(result["ok"])

    def test_stent_described(self):
        text = "Findings: a stent is patent in the mid LAD. CAD-RADS 3/P2/S"
        result = validate.check(text, case(label="CAD-RADS 3/P2/S", modifiers=["S"]), CFG)
        self.assertTrue(result["results"]["modifiers"]["ok"])

    def test_incidental_finding_described(self):
        finding = {"id": "atelectasis", "organ": "lungs", "label": "dependent atelectasis",
                   "keywords": ["atelecta"]}
        text = ("Findings: the LAD shows 30% stenosis. There is dependent atelectasis of the "
                "lung bases. CAD-RADS 2/P2")
        result = validate.check(text, case(label="CAD-RADS 2/P2", incidental=[finding]), CFG)
        self.assertTrue(result["results"]["incidental"]["ok"])
        self.assertFalse(result["results"]["incidental"]["skipped"])

    def test_incidental_finding_absent(self):
        finding = {"id": "atelectasis", "organ": "lungs", "label": "dependent atelectasis",
                   "keywords": ["atelecta"]}
        text = "Findings: the LAD shows 30% stenosis. CAD-RADS 2/P2"
        result = validate.check(text, case(label="CAD-RADS 2/P2", incidental=[finding]), CFG)
        self.assertFalse(result["results"]["incidental"]["ok"])

    def test_finding_without_keywords_is_ignored(self):
        finding = {"id": "x", "organ": "lungs", "label": "l", "keywords": []}
        result = validate.check("Findings: x. CAD-RADS 0", case(label="CAD-RADS 0", incidental=[finding]), CFG)
        self.assertTrue(result["results"]["incidental"]["skipped"])
        self.assertTrue(result["ok"])

    def test_check_disabled_without_finding(self):
        result = validate.check("Findings: x. CAD-RADS 0", case(label="CAD-RADS 0"), CFG)
        self.assertTrue(result["results"]["incidental"]["skipped"])

    def test_modifier_e_matches_common_wording(self):
        # real reports say "non-atherosclerotic abnormality" or "fusiform aneurysm",
        # not necessarily "anomaly"
        for wording in ("a fusiform aneurysm of the proximal RCA",
                        "this represents a non-atherosclerotic abnormality",
                        "an anomalous origin of the RCA"):
            with self.subTest(wording=wording):
                result = validate.check(f"Findings: {wording}. CAD-RADS 2/P2/E",
                                        case(label="CAD-RADS 2/P2/E", modifiers=["E"]), CFG)
                self.assertTrue(result["results"]["modifiers"]["ok"], wording)

    def test_forbidden_phrase(self):
        result = validate.check("As an AI I cannot. CAD-RADS 0", case(label="CAD-RADS 0"), CFG)
        self.assertFalse(result["results"]["forbidden"]["ok"])


class TestUnplannedIncidental(unittest.TestCase):
    """The check that looks at findings the planner did NOT draw.

    `describe_normal` invites a paragraph on the lungs / pleura / aorta: the model
    may add a minor abnormality there. Flagging must not fire on the ordinary
    "unremarkable / no effusion" wording, which is what a normal report contains.
    """

    @classmethod
    def setUpClass(cls):
        cls.catalogue = incidental.load(ROOT / "reference" / "incidental_findings.json")

    def _check(self, text, incidental_findings=(), language="en", cfg=None):
        return validate.check(
            text,
            case(label="CAD-RADS 2/P2", incidental=incidental_findings, language=language),
            cfg or CFG,
            self.catalogue,
        )

    def test_generic_normal_paragraph_is_not_flagged(self):
        text = ("Findings: the LAD shows 30% stenosis. The lungs are clear, the pleura is "
                "unremarkable, there is no pericardial effusion, the thoracic aorta is of "
                "normal caliber and the mediastinum is unremarkable. CAD-RADS 2/P2")
        entry = self._check(text)["results"]["unplanned_incidental"]
        self.assertTrue(entry["ok"], entry["detected"])
        self.assertFalse(entry["skipped"])

    def test_added_minor_finding_is_flagged(self):
        text = ("Findings: the LAD shows 30% stenosis. There is minimal dependent "
                "atelectasis of the lung bases. CAD-RADS 2/P2")
        result = self._check(text)
        entry = result["results"]["unplanned_incidental"]
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["status"], "warn")
        self.assertIn("lungs", entry["detected"][0]["organs"])
        # warn: the report is still accepted
        self.assertTrue(result["ok"])

    def test_negated_finding_is_never_flagged(self):
        samples = [
            ("en", "There is no dependent atelectasis of the lung bases."),
            ("en", "No pericardial effusion is seen."),
            ("en", "The pericardium is unremarkable."),
            ("fr", "Il n'y a pas d'épanchement péricardique."),
            ("de", "Der Perikarderguss ist nicht nachweisbar."),
            ("de", "Die Lunge ist unauffällig."),
            ("it", "Non vi è versamento pericardico."),
        ]
        for language, sentence in samples:
            with self.subTest(language=language, sentence=sentence):
                result = self._check(f"Findings: x. {sentence} CAD-RADS 2/P2", language=language)
                self.assertTrue(result["results"]["unplanned_incidental"]["ok"], sentence)

    def test_planned_finding_is_not_flagged(self):
        finding = {"id": "dependent_atelectasis", "organ": "lungs",
                   "label": "dependent atelectasis", "keywords": ["atelecta"]}
        text = ("Findings: the LAD shows 30% stenosis. There is minimal dependent "
                "atelectasis of the lung bases. CAD-RADS 2/P2")
        entry = self._check(text, incidental_findings=[finding])["results"]["unplanned_incidental"]
        self.assertTrue(entry["ok"], entry["detected"])

    def test_planned_heart_finding_covers_the_aortic_valve(self):
        # "aortic valve" matches the aorta keywords AND the heart keywords: the
        # planned heart finding must absorb it, or every calcified valve would be
        # reported as an extra aortic finding.
        finding = {"id": "aortic_valve_calcification", "organ": "heart",
                   "label": "aortic valve calcification", "keywords": ["valve"]}
        text = ("Findings: x. Calcification of the aortic valve without significant "
                "stenosis. CAD-RADS 2/P2")
        entry = self._check(text, incidental_findings=[finding])["results"]["unplanned_incidental"]
        self.assertTrue(entry["ok"], entry["detected"])

    def test_upper_abdomen_extra_french(self):
        text = ("Findings: x. Une stéatose hépatique modérée est visible. "
                "CAD-RADS 2/P2")
        entry = self._check(text, language="fr")["results"]["unplanned_incidental"]
        self.assertFalse(entry["ok"])
        self.assertIn("upper_abdomen", entry["detected"][0]["organs"])

    def test_without_catalogue_is_skipped(self):
        result = validate.check(
            "Findings: there is mild dependent atelectasis. CAD-RADS 2/P2",
            case(label="CAD-RADS 2/P2"), CFG,
        )
        entry = result["results"]["unplanned_incidental"]
        self.assertTrue(entry["skipped"])
        self.assertEqual(entry["status"], "off")
        self.assertTrue(result["ok"])

    def test_policy_off_disables_the_check(self):
        cfg = {"checks": dict(CFG["checks"], unplanned_incidental="off")}
        text = "Findings: minimal dependent atelectasis of the lung bases. CAD-RADS 2/P2"
        entry = self._check(text, cfg=cfg)["results"]["unplanned_incidental"]
        self.assertEqual(entry["status"], "off")

    def test_policy_fail_excludes_the_report(self):
        cfg = {"checks": dict(CFG["checks"], unplanned_incidental="fail")}
        text = "Findings: minimal dependent atelectasis of the lung bases. CAD-RADS 2/P2"
        result = self._check(text, cfg=cfg)
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"]["unplanned_incidental"]["status"], "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
