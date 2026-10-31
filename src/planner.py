"""Building the generation plan.

Three responsibilities:
1. Draw the cases reproducibly (seed) and stratified by target category.
2. Turn each case into a complete, consistent CLINICAL SCENARIO.
3. Have the CAD-RADS label COMPUTED by src/classify.py from the lesions.

Key point: the label is never left to the LLM's judgement. The target category is
used to generate a plausible scenario, then the REAL category is recomputed from
the lesions. If the two diverge, the computed value wins (source of truth) and the
divergence is reported: this is a plan-drift metric, useful for the paper.
"""
from __future__ import annotations

import random

from . import classify as rules
from . import incidental as incidental_lib

CADRADS_ORDER = ["0", "1", "2", "3", "4A", "4B", "5"]

PLAQUE_TYPES = ["calcified", "mixed", "non-calcified"]

# Calcium consistent with the burden (Agatston order of magnitude).
AGATSTON_BY_BURDEN = {
    "P1": (1, 100),
    "P2": (100, 300),
    "P3": (300, 800),
    "P4": (800, 2000),
}

GRAFTS = ["LIMA to LAD", "RIMA to RCA", "saphenous vein graft to LCX"]


def allocate(n: int, weights: dict) -> dict:
    """Spread n cases over the weights, largest remainder (exact total)."""
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
    """Lesion scenario consistent with the target category."""
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
        # 1 or 2 vessels at 70-99 %, no left main, no three-vessel disease.
        vessels = rng.sample(["LAD", "LCX", "RCA"], k=rng.choice([1, 1, 2]))
        return [_lesion(rng, reference, v, rng.randint(70, 99)) for v in vessels]

    if cadrads == "4B":
        # Either left main >= 50 %, or three vessels >= 70 %.
        if rng.random() < 0.5:
            return [_lesion(rng, reference, "LM", rng.randint(50, 99)),
                    _lesion(rng, reference, rng.choice(["LAD", "LCX"]), rng.randint(70, 99))]
        return [_lesion(rng, reference, v, rng.randint(70, 99)) for v in ("LAD", "LCX", "RCA")]

    # CAD-RADS 5: at least one total occlusion
    occluded = rng.choice(["LAD", "LCX", "RCA"])
    lesions = [_lesion(rng, reference, occluded, 100)]
    if rng.random() < 0.5:
        other = rng.choice([v for v in ("LAD", "LCX", "RCA") if v != occluded])
        lesions.append(_lesion(rng, reference, other, rng.randint(1, 69)))
    return lesions


def _pick_modifiers(rng, target, weights):
    """Draw the modifiers.

    - S, G and V imply atherosclerosis: impossible in CAD-RADS 0.
    - N is excluded for targets 0/1/2: a non-diagnostic segment combined with a
      stenosis < 50 % reclassifies the case as CAD-RADS N (rule 1 of the engine),
      which would shift the requested distribution.
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
    """A non-diagnostic segment, on a vessel without a severe lesion."""
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

    # Interleaved order: avoids blocks of identical cases, and a style drift if the
    # model "warms up" over the session.
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

        # --- label computation (source of truth) --------------------------
        category, _ = rules.classify_category(lesions, non_evaluable)
        choices = rules.plaque_burden_choices(category, lesions)
        burden = rng.choice(choices) if choices else None
        classified = rules.classify(lesions, modifiers, non_evaluable, burden)

        if classified["category"] != target:
            classified["trace"].append(
                f"WARNING: target category {target} but computed category "
                f"{classified['category']} — the computed value is kept"
            )

        # --- calcium consistent with the burden ----------------------------
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

        # --- minor incidental findings (optional) --------------------------
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
