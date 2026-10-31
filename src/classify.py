"""Moteur de classification CAD-RADS 2.0 — calcul deterministe.

Le label final n'est JAMAIS produit par le LLM. Il est calcule ici a partir
des lesions, puis impose au modele dans le prompt. Consequence : le label et
les lesions decrites dans le prompt ne peuvent pas etre incoherents.

Reference : Cury RC et al., CAD-RADS 2.0, Radiol Cardiothorac Imaging
2022;4(5):e220183. doi:10.1148/ryct.220183

Precedence appliquee (par patient, sur la stenose la plus severe) :

    1. segment non diagnostique et aucune stenose >= 50 %  -> N
    2. occlusion totale (100 %)                            -> 5
    3. tronc commun >= 50 % ou 3 vaisseaux >= 70 %          -> 4B
    4. >= 70 % (1 ou 2 vaisseaux)                           -> 4A
    5. 50-69 %                                              -> 3
    6. 25-49 %                                              -> 2
    7. 1-24 %                                               -> 1
    8. aucune plaque                                        -> 0
"""
from __future__ import annotations

THREE_VESSELS = ("LAD", "LCX", "RCA")
MODIFIER_ORDER = ("N", "S", "G", "E", "V")

# Plaque burden admissible par categorie (coherence clinique).
BURDEN_BY_CATEGORY = {
    "0": [],
    "N": [],
    "1": ["P1", "P2"],
    "2": ["P1", "P2", "P3"],
    "3": ["P2", "P3"],
    "4A": ["P2", "P3", "P4"],
    "4B": ["P3", "P4"],
    "5": ["P3", "P4"],
}

# Plaque burden admissible selon le nombre de vaisseaux porteurs de plaque :
# P est une mesure d'etendue, pas seulement de severite.
BURDEN_BY_EXTENT = {
    0: [],
    1: ["P1", "P2"],
    2: ["P2", "P3"],
    3: ["P3", "P4"],
}


def _max_per_vessel(lesions) -> dict:
    per_vessel: dict[str, int] = {}
    for lesion in lesions:
        vessel = lesion["vessel"]
        stenosis = int(lesion["stenosis_pct"])
        per_vessel[vessel] = max(per_vessel.get(vessel, 0), stenosis)
    return per_vessel


def classify_category(lesions, non_evaluable=()) -> tuple[str, list]:
    """Categorie CAD-RADS + trace de justification (pour l'audit et le papier)."""
    per_vessel = _max_per_vessel(lesions)
    max_stenosis = max(per_vessel.values(), default=0)
    severe_vessels = [v for v in THREE_VESSELS if per_vessel.get(v, 0) >= 70]
    left_main = per_vessel.get("LM", 0)

    trace = [f"stenose maximale = {max_stenosis}%"]
    if left_main:
        trace.append(f"tronc commun = {left_main}%")
    trace.append(f"vaisseaux >= 70% = {len(severe_vessels)} {severe_vessels}")
    if non_evaluable:
        trace.append(f"segments non diagnostiques = {list(non_evaluable)}")

    if non_evaluable and max_stenosis < 50:
        trace.append("regle 1 : non diagnostique sans stenose >= 50% -> N")
        return "N", trace
    if max_stenosis >= 100:
        trace.append("regle 2 : occlusion totale -> 5")
        return "5", trace
    if left_main >= 50 or len(severe_vessels) >= 3:
        trace.append("regle 3 : tronc commun >= 50% ou 3 vaisseaux >= 70% -> 4B")
        return "4B", trace
    if max_stenosis >= 70:
        trace.append("regle 4 : stenose >= 70% -> 4A")
        return "4A", trace
    if max_stenosis >= 50:
        trace.append("regle 5 : stenose 50-69% -> 3")
        return "3", trace
    if max_stenosis >= 25:
        trace.append("regle 6 : stenose 25-49% -> 2")
        return "2", trace
    if max_stenosis >= 1:
        trace.append("regle 7 : stenose 1-24% -> 1")
        return "1", trace

    trace.append("regle 8 : aucune plaque -> 0")
    return "0", trace


def plaque_burden_choices(category: str, lesions) -> list:
    """P possibles : coherents avec la categorie ET avec l'etendue constatee.

    Le consensus definit P de facon semi-quantitative (SIS, nombre de
    segments) ; il n'existe pas de formule fermee. On contraint donc P au lieu
    de le calculer, puis on verifie la coherence.
    """
    by_category = set(BURDEN_BY_CATEGORY.get(category, []))
    n_vessels = len({l["vessel"] for l in lesions if int(l["stenosis_pct"]) > 0})
    by_extent = set(BURDEN_BY_EXTENT.get(min(n_vessels, 3), []))
    both = sorted(by_category & by_extent)
    return both or sorted(by_category)


def filter_modifiers(modifiers, category: str, non_evaluable=()) -> list:
    """Ne conserve que les modificateurs qui ont un sens clinique.

    - N est deduit de la presence d'un segment non diagnostique (source unique
      de verite), et ne s'applique pas si la categorie EST N.
    - S, G et V supposent une atherosclerose : impossibles en CAD-RADS 0.
    """
    kept = []
    for modifier in MODIFIER_ORDER:
        if modifier == "N":
            if non_evaluable and category != "N":
                kept.append(modifier)
            continue
        if modifier not in modifiers:
            continue
        if category in ("0", "N") and modifier != "E":
            continue
        kept.append(modifier)
    return kept


def build_label(category: str, plaque_burden=None, modifiers=()) -> str:
    parts = [f"CAD-RADS {category}"]
    if plaque_burden:
        parts.append(plaque_burden)
    parts.extend(modifiers)
    return "/".join(parts)


def classify(lesions, modifiers=(), non_evaluable=(), plaque_burden=None) -> dict:
    """Classification complete d'un cas : categorie + P + modificateurs + label."""
    category, trace = classify_category(lesions, non_evaluable)
    choices = plaque_burden_choices(category, lesions)

    if category == "N":
        plaque_burden = None  # la categorie N n'est pas accompagnee de P
    elif plaque_burden is None:
        plaque_burden = choices[0] if choices else None
    elif choices and plaque_burden not in choices:
        trace.append(
            f"ATTENTION : plaque burden {plaque_burden} hors de l'ensemble coherent {choices}"
        )

    kept = filter_modifiers(modifiers, category, non_evaluable)
    dropped = [m for m in modifiers if m not in kept]
    if dropped:
        trace.append(f"modificateurs ecartes (incoherents) = {dropped}")

    return {
        "category": category,
        "plaque_burden": plaque_burden,
        "modifiers": kept,
        "label": build_label(category, plaque_burden, kept),
        "trace": trace,
    }
