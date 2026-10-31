"""Tests for the dependency-free XLSX writer and the dataset descriptor.

    python3 tests/test_xlsx.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import store, xlsx  # noqa: E402


class TestWriter(unittest.TestCase):
    def test_column_letters(self):
        self.assertEqual(xlsx.column_letter(1), "A")
        self.assertEqual(xlsx.column_letter(26), "Z")
        self.assertEqual(xlsx.column_letter(27), "AA")
        self.assertEqual(xlsx.column_letter(46), "AT")

    def test_archive_holds_the_required_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = xlsx.write(Path(tmp) / "t.xlsx", ["a", "b"], [[1, "x"]])
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            for part in ("[Content_Types].xml", "xl/workbook.xml", "xl/styles.xml",
                         "xl/worksheets/sheet1.xml"):
                self.assertIn(part, names)

    def test_cells_are_written_and_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = xlsx.write(Path(tmp) / "t.xlsx", ["texte", "nombre"],
                              [["a<b>&c", 42]])
            sheet = zipfile.ZipFile(path).read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("a&lt;b&gt;&amp;c", sheet)
            self.assertIn("<v>42</v>", sheet)

    def test_empty_values_leave_the_cell_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = xlsx.write(Path(tmp) / "t.xlsx", ["a", "b"], [["x", None]])
            sheet = zipfile.ZipFile(path).read("xl/worksheets/sheet1.xml").decode()
            self.assertNotIn('r="B2"', sheet)


class TestDescriptor(unittest.TestCase):
    def _store(self, tmp, cfg):
        return store.RunStore(Path(tmp), cfg, "test", dry_run=False)

    def test_one_row_per_report_with_every_input(self):
        cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        cases = [
            {
                "case_id": "SYN-0001", "cadrads": "4A", "target_cadrads": "4A", "target_ok": True,
                "label": "CAD-RADS 4A/P2/S", "plaque_burden": "P2", "modifiers": ["S"],
                "lesions": [{"vessel": "LAD", "segment": "proximal", "stenosis_pct": 84, "plaque": "mixed"}],
                "per_lesion": [{"vessel": "LAD", "segment": "proximal", "stenosis_pct": 84, "cadrads": "4"}],
                "agatston": 312, "age": 71, "sex": "female", "indication": "stable",
                "language": "en", "style": "structured", "technique": "retrospective",
                "stent_site": "RCA mid", "graft_site": None, "vulnerable_site": None,
                "exception_site": None, "non_evaluable": [], "incidental": [],
            },
        ]
        records = [{
            "case_id": "SYN-0001", "status": "ok", "file": "reports/SYN-0001_4A_en.txt",
            "checks": {"results": {"length": {"words": 120}, "label": {"ok": True},
                                   "modifiers": {"ok": True}, "incidental": {"ok": True}}},
            "meta": {"attempts": 1, "latency_s": 7.5, "model": "m", "prompt_sha": "abc"},
        }]
        with tempfile.TemporaryDirectory() as tmp:
            run = self._store(tmp, cfg)
            run.init()
            path = run.write_dataset_descriptor(cases, records)
            self.assertIsNotNone(path)
            sheet = zipfile.ZipFile(path).read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("SYN-0001", sheet)
            self.assertIn("CAD-RADS 4A/P2/S", sheet)
            self.assertIn("<v>84</v>", sheet)
            self.assertIn("<v>312</v>", sheet)
            self.assertIn("RCA mid", sheet)

    def test_descriptor_can_be_disabled(self):
        cfg = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
        cfg["export"]["write_xlsx"] = False
        with tempfile.TemporaryDirectory() as tmp:
            run = self._store(tmp, cfg)
            run.init()
            self.assertIsNone(run.write_dataset_descriptor([], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
