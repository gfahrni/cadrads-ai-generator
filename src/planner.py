"""Construction du plan de generation.

Trois responsabilites :
1. Tirer les cas de facon reproductible (seed) et stratifiee par categorie visee.
2. Transformer chaque cas en SCENARIO CLINIQUE complet et coherent.
3. Faire CALCULER le label CAD-RADS par src/classify.py a partir des lesions.

Point cle : le label n'est jamais laisse a l'appreciation du LLM. La categorie
visee sert a generer un scenario plausible, puis la categorie REELLE est
recalculee a partir des lesions. Si les deux divergent, on garde la valeur
calculee (source de verite) et on le signale : c'est une mesure de derive du
plan, utile pour le papier.
"""
from __future__ import annotations

import random

from . import classify as rules
from . import incidental as incidental_lib

CADRADS_ORDER = ["0", "1", "2", "3", "4A", "4B", "5"]

PLAQUE_TYPES = ["calcified", "mixed", "non-calcified"]

# Calcium coherent avec le burden (ordre de grandeur Agatston).
AGATSTON_BY_BURDEN = {
    "P1": (1, 100),
    "P2": (100, 300),
    "P3": (300, 800),
    "P4": (800, 2000),
}

GRAFTS = ["LIMA to LAD", "RIMA to RCA", "saphenous vein graft to LCX"]


def allocate(n: int, weights: dict) -> dict:
    """Repartit n cas selon les poids, par plus forts restes (total exact)."""
    total = sum(weights.values())
    exact = {k: n * v / total for k, v in weights.items()}
    counts = {k: int(v) for k, v in exact.items()}
    missing = n - sum(counts.values())
    ranked = sorted(exact, key=lambda k: (exact[k] - counts[k]), reverse=True)
    for key in ranked[:missing]:
        counts[key] += 1
    return counts


def _sample_segment(rng, reference, vessel):
    return rng.choice(reference["vessels"][vessel])


def _lesion(rng, reference, vessel, stenosis_pct):
    return {
        "vessel": vessel,
        "segment": _sample_segment(rng, reference, vessel),
        "stenosis_pct": stenosis_pct,
        "plaque": rng.choice(PLAQUE_TYPES),
    }


def _lesions_for(rng, reference, cadrads):
    """Scenario lesionnel coherent avec la categorie visee."""
    if cadrads == "0":
        return []

    if cadrads == "1":
        return [_lesion(rng, reference, rng.choice(["LAD", "LCX", "RCA"]), rng.randint(1, 24))]

    if cadrads == "2":
        vessels = rng.sample(["LAD", "LCX", "RCA"], k=rng.choice([1, 1, 2]))
        return [_lesion(rng, reference, v, rng.randint(25, 49)) for v in vessels]

    if cadrads == "3":
        return [_lesion(rng, reference, rng.choice(["LAD", "LCX", "RCA"]), rng.randint(50, 69))]

    if cadrads == "4A":
        # 1 ou 2 vaisseaux a 70-99 %, sans tronc commun, sans 3 vaisseaux.
        vessels = rng.sample(["LAD", "LCX", "RCA"], k=rng.choice([1, 1, 2]))
        return [_lesion(rng, reference, v, rng.randint(70, 99)) for v in vessels]

    if cadrads == "4B":
        # Soit tronc commun >= 50 %, soit 3 vaisseaux >= 70 %.
        if rng.random() < 0.5:
            return [_lesion(rng, reference, "LM", rng.randint(50, 99)),
                    _lesion(rng, reference, rng.choice(["LAD", "LCX"]), rng.randint(70, 99))]
        return [_lesion(rng, reference, v, rng.randint(70, 99)) for v in ("LAD", "LCX", "RCA")]

    # CAD-RADS 5 : au moins une occlusion totale
    occluded = rng.choice(["LAD", "LCX", "RCA"])
    lesions = [_lesion(rng, reference, occluded, 100)]
    if rng.random() < 0.5:
        other = rng.choice([v for v in ("LAD", "LCX", "RCA") if v != occluded])
        lesions.append(_lesion(rng, reference, other, rng.randint(1, 69)))
    return lesions


def _pick_modifiers(rng, target, weights):
    """Tirage des modificateurs.

    - S, G et V supposent une atherosclerose : impossibles en CAD-RADS 0.
    - N est exclu pour les cibles 0/1/2 : un segment non diagnostique associe a
      une stenose < 50 % reclasse le cas en CAD-RADS N (regle 1 du moteur), ce
      qui ferait deriver la distribution demandee.
    """
    chosen = []
    for modifier, weight in weights.items():
        if weight <= 0:
            continue
        if target == "0" and modifier != "E":
            continue
        if modifier == "N" and target in ("0", "1", "2"):
            continue
        if rng.random() < weight:
            chosen.append(modifier)
    return sorted(chosen)


def _non_evaluable(rng, reference, lesions):
    """Un segment non diagnostique, sur un vaisseau sans lesion severe."""
    severe = {l["vessel"] for l in lesions if l["stenosis_pct"] >= 50}
    candidates = [v for v in reference["vessels"] if v not in severe]
    if not candidates:
        return []
    vessel = rng.choice(candidates)
    return [f"{vessel} {_sample_segment(rng, reference, vessel)}"]


def build_cases(cfg: dict, reference: dict, catalogue: dict | None = None) -> list:
    gen = cfg["generation"]
    run = cfg["run"]
    rng = random.Random(run["seed"])

    counts = allocate(run["n_reports"], gen["cadrads_weights"])
    order = [c for c in CADRADS_ORDER if counts.get(c, 0) > 0]

    # Ordre interleave : evite des blocs de cas identiques, et une derive de
    # style si le modele "s'echauffe" au fil de la session.
    queue = []
    for category in order:
        queue.extend([category] * counts[category])
    rng.shuffle(queue)

    age_lo, age_hi = gen["age_range"]
    cases = []

    for index, target in enumerate(queue, start=1):
        style = gen["style"]
        if style == "random":
            style = rng.choice(["free", "structured"])

        sex = gen["sex"]
        if sex == "random":
            sex = rng.choice(["male", "female"])

        age = rng.randint(age_lo, age_hi)
        language = rng.choice(gen["languages"])

        lesions = _lesions_for(rng, reference, target)
        modifiers = _pick_modifiers(rng, target, gen["modifier_weights"])
        non_evaluable = _non_evaluable(rng, reference, lesions) if "N" in modifiers else []

        # --- calcul du label (source de verite) ---------------------------
        category, _ = rules.classify_category(lesions, non_evaluable)
        choices = rules.plaque_burden_choices(category, lesions)
        burden = rng.choice(choices) if choices else None
        classified = rules.classify(lesions, modifiers, non_evaluable, burden)

        if classified["category"] != target:
            classified["trace"].append(
                f"ATTENTION : categorie visee {target} mais categorie calculee "
                f"{classified['category']} — la valeur calculee est retenue"
            )

        # --- calcium coherent avec le burden ------------------------------
        if classified["plaque_burden"]:
            low, high = AGATSTON_BY_BURDEN.get(classified["plaque_burden"], (0, 100))
            agatston = rng.randint(low, high)
        else:
            agatston = 0

        stent_site = None
        if "S" in classified["modifiers"] and lesions:
            site = rng.choice(lesions)
            stent_site = f"{site['vessel']} {site['segment']}"
        graft_site = rng.choice(GRAFTS) if "G" in classified["modifiers"] else None

        # --- decouvertes fortuites mineures (option) ----------------------
        incidental = []
        if catalogue is not None:
            for finding in incidental_lib.sample(rng, age, sex, cfg, catalogue):
                incidental.append({
                    "id": finding["id"],
                    "organ": finding["organ"],
                    "label": finding["label_en"],
                    "keywords": incidental_lib.keywords_for(
                        finding, finding["organ"], language, catalogue
                    ),
                })

        cases.append({
            "case_id": f"SYN-{index:04d}",
            "cadrads": classified["category"],
            "target_cadrads": target,
            "target_ok": classified["category"] == target,
            "label": classified["label"],
            "lesions": lesions,
            "plaque_burden": classified["plaque_burden"],
            "modifiers": classified["modifiers"],
            "declared_modifiers": modifiers,
            "stent_site": stent_site,
            "graft_site": graft_site,
            "non_evaluable": non_evaluable,
            "agatston": agatston,
            "classification_trace": classified["trace"],
            "incidental": incidental,
            "language": language,
            "style": style,
            "age": age,
            "sex": sex,
            "indication": gen["indication"],
        })

    return cases
