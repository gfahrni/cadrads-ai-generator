"""Tests du moteur de classification CAD-RADS 2.0.

    python3 tests/test_classify.py

Aucune dependance. Ces tests sont la garantie que le label impose au LLM est
conforme aux regles : a joindre au depot pour la reproductibilite.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.classify import classify, classify_category  # noqa: E402


def lesion(vessel, pct, segment="mid", plaque="calcified"):
    return {"vessel": vessel, "segment": segment, "stenosis_pct": pct, "plaque": plaque}


class TestCategorie(unittest.TestCase):
    def test_absence_de_plaque(self):
        self.assertEqual(classify_category([])[0], "0")

    def test_seuils_de_stenose(self):
        for pct, expected in [(1, "1"), (24, "1"), (25, "2"), (49, "2"),
                              (50, "3"), (69, "3"), (70, "4A"), (99, "4A")]:
            with self.subTest(pct=pct):
                self.assertEqual(classify_category([lesion("LAD", pct)])[0], expected)

    def test_occlusion_totale(self):
        self.assertEqual(classify_category([lesion("RCA", 100)])[0], "5")

    def test_tronc_commun(self):
        self.assertEqual(classify_category([lesion("LM", 55), lesion("LAD", 80)])[0], "4B")
        # LM a 49% ne suffit pas
        self.assertEqual(classify_category([lesion("LM", 49), lesion("LAD", 80)])[0], "4A")

    def test_trois_vaisseaux(self):
        trois = [lesion("LAD", 75), lesion("LCX", 80), lesion("RCA", 70)]
        self.assertEqual(classify_category(trois)[0], "4B")
        # deux vaisseaux seulement -> 4A
        self.assertEqual(classify_category(trois[:2])[0], "4A")

    def test_deux_vaisseaux_severes_restent_4A(self):
        self.assertEqual(classify_category([lesion("LAD", 90), lesion("LCX", 85)])[0], "4A")

    def test_precedence_5_sur_4B(self):
        # occlusion + tronc commun : la convention retenue donne 5
        self.assertEqual(classify_category([lesion("LAD", 100), lesion("LM", 60)])[0], "5")

    def test_non_diagnostique(self):
        # aucune stenose >= 50% + segment non diagnostique -> N
        self.assertEqual(classify_category([lesion("LAD", 30)], non_evaluable=["RCA mid"])[0], "N")
        self.assertEqual(classify_category([], non_evaluable=["RCA mid"])[0], "N")
        # stenose >= 50% : la categorie tient, N devient un modificateur
        result = classify([lesion("LAD", 60)], modifiers=["N"], non_evaluable=["RCA mid"])
        self.assertEqual(result["category"], "3")
        self.assertIn("N", result["modifiers"])
        self.assertEqual(result["label"], "CAD-RADS 3/P2/N")


class TestLabel(unittest.TestCase):
    def test_label_simple(self):
        self.assertEqual(classify([lesion("LAD", 80)], plaque_burden="P2")["label"],
                         "CAD-RADS 4A/P2")

    def test_label_avec_modificateur(self):
        result = classify([lesion("LAD", 80)], modifiers=["S"], plaque_burden="P3")
        self.assertEqual(result["label"], "CAD-RADS 4A/P3/S")

    def test_ordre_des_modificateurs(self):
        result = classify([lesion("LAD", 60)], modifiers=["S", "G", "E"],
                          non_evaluable=["RCA mid"], plaque_burden="P2")
        self.assertEqual(result["label"], "CAD-RADS 3/P2/N/S/G/E")

    def test_modificateurs_ecartes_en_cad_rads_0(self):
        result = classify([], modifiers=["S", "V", "G", "E"])
        self.assertEqual(result["category"], "0")
        self.assertEqual(result["modifiers"], ["E"])
        self.assertIsNone(result["plaque_burden"])

    def test_modificateur_n_sans_segment_non_diagnostique(self):
        result = classify([lesion("LAD", 60)], modifiers=["N"])
        self.assertEqual(result["modifiers"], [])


class TestBurden(unittest.TestCase):
    def test_burden_coherent_avec_une_seule_lesion(self):
        # P4 (extensif) est impossible avec un seul vaisseau atteint
        result = classify([lesion("LAD", 80)], plaque_burden="P4")
        self.assertEqual(result["plaque_burden"], "P4")
        self.assertTrue(any("ATTENTION" in ligne for ligne in result["trace"]))

    def test_burden_par_defaut(self):
        self.assertIn(classify([lesion("LAD", 80)])["plaque_burden"], ("P2", "P3"))

    def test_pas_de_burden_si_categorie_n(self):
        result = classify([], non_evaluable=["RCA mid"])
        self.assertEqual(result["category"], "N")
        self.assertIsNone(result["plaque_burden"])
        self.assertEqual(result["label"], "CAD-RADS N")


class TestScenariosPlausibles(unittest.TestCase):
    def test_cas_4b_tronc_commun_avec_stent(self):
        lesions = [lesion("LM", 55, "ostial"), lesion("LAD", 87, "proximal")]
        result = classify(lesions, modifiers=["S"], plaque_burden="P3")
        self.assertEqual(result["label"], "CAD-RADS 4B/P3/S")

    def test_cas_diffus_trois_vaisseaux(self):
        lesions = [lesion("LAD", 75), lesion("LCX", 80), lesion("RCA", 70)]
        self.assertEqual(classify(lesions, plaque_burden="P4")["label"], "CAD-RADS 4B/P4")


if __name__ == "__main__":
    unittest.main(verbosity=2)
