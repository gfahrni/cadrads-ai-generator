"""Tests for response cleaning and the automatic checks.

    python3 tests/test_validate.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import validate  # noqa: E402

CFG = {
    "checks": {
        "label_integrity": "fail",
        "language_match": "warn",
        "modifier_mention": "warn",
        "incidental_mention": "warn",
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

    def test_forbidden_phrase(self):
        result = validate.check("As an AI I cannot. CAD-RADS 0", case(label="CAD-RADS 0"), CFG)
        self.assertFalse(result["results"]["forbidden"]["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
