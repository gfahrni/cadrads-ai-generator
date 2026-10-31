"""CAD-RADS 2.0 classification engine — deterministic computation.

The final label is NEVER produced by the LLM. It is computed here from the
lesions, then imposed on the model in the prompt. Consequence: the label and
the lesions described in the prompt cannot be inconsistent.

Reference: Cury RC et al., CAD-RADS 2.0, Radiol Cardiothorac Imaging
2022;4(5):e220183. doi:10.1148/ryct.220183

Precedence applied (per patient, on the most severe stenosis):

    1. non-diagnostic segment and no stenosis >= 50 %    -> N
    2. total occlusion (100 %)                           -> 5
    3. left main >= 50 % or 3 vessels >= 70 %            -> 4B
    4. >= 70 % (1 or 2 vessels)                          -> 4A
    5. 50-69 %                                           -> 3
    6. 25-49 %                                           -> 2
    7. 1-24 %                                            -> 1
    8. no plaque                                         -> 0
"""
from __future__ import annotations

THREE_VESSELS = ("LAD", "LCX", "RCA")
MODIFIER_ORDER = ("N", "S", "G", "E", "V")

# Plaque burden allowed by category (clinical consistency).
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

# Plaque burden allowed by number of diseased vessels:
# P is a measure of extent, not only of severity.
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
    """CAD-RADS category plus a justification trace (for audit and for the paper)."""
    per_vessel = _max_per_vessel(lesions)
    max_stenosis = max(per_vessel.values(), default=0)
    severe_vessels = [v for v in THREE_VESSELS if per_vessel.get(v, 0) >= 70]
    left_main = per_vessel.get("LM", 0)

    trace = [f"max stenosis = {max_stenosis}%"]
    if left_main:
        trace.append(f"left main = {left_main}%")
    trace.append(f"vessels >= 70% = {len(severe_vessels)} {severe_vessels}")
    if non_evaluable:
        trace.append(f"non-diagnostic segments = {list(non_evaluable)}")

    if non_evaluable and max_stenosis < 50:
        trace.append("rule 1: non-diagnostic with no stenosis >= 50% -> N")
        return "N", trace
    if max_stenosis >= 100:
        trace.append("rule 2: total occlusion -> 5")
        return "5", trace
    if left_main >= 50 or len(severe_vessels) >= 3:
        trace.append("rule 3: left main >= 50% or 3 vessels >= 70% -> 4B")
        return "4B", trace
    if max_stenosis >= 70:
        trace.append("rule 4: stenosis >= 70% -> 4A")
        return "4A", trace
    if max_stenosis >= 50:
        trace.append("rule 5: stenosis 50-69% -> 3")
        return "3", trace
    if max_stenosis >= 25:
        trace.append("rule 6: stenosis 25-49% -> 2")
        return "2", trace
    if max_stenosis >= 1:
        trace.append("rule 7: stenosis 1-24% -> 1")
        return "1", trace

    trace.append("rule 8: no plaque -> 0")
    return "0", trace


def plaque_burden_choices(category: str, lesions) -> list:
    """Possible P values: consistent with the category AND with the observed extent.

    The consensus defines P semi-quantitatively (SIS, number of segments); there is
    no closed formula. P is therefore constrained rather than computed, and the
    consistency is then checked.
    """
    by_category = set(BURDEN_BY_CATEGORY.get(category, []))
    n_vessels = len({l["vessel"] for l in lesions if int(l["stenosis_pct"]) > 0})
    by_extent = set(BURDEN_BY_EXTENT.get(min(n_vessels, 3), []))
    both = sorted(by_category & by_extent)
    return both or sorted(by_category)


def filter_modifiers(modifiers, category: str, non_evaluable=()) -> list:
    """Keep only the modifiers that make clinical sense.

    - N is derived from the presence of a non-diagnostic segment (single source of
      truth), and does not apply when the category IS N.
    - S, G and V imply atherosclerosis: impossible in CAD-RADS 0.
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
    """Full classification of a case: category + P + modifiers + label."""
    category, trace = classify_category(lesions, non_evaluable)
    choices = plaque_burden_choices(category, lesions)

    if category == "N":
        plaque_burden = None  # category N is not accompanied by a P value
    elif plaque_burden is None:
        plaque_burden = choices[0] if choices else None
    elif choices and plaque_burden not in choices:
        trace.append(
            f"WARNING: plaque burden {plaque_burden} outside the consistent set {choices}"
        )

    kept = filter_modifiers(modifiers, category, non_evaluable)
    dropped = [m for m in modifiers if m not in kept]
    if dropped:
        trace.append(f"modifiers dropped (inconsistent) = {dropped}")

    return {
        "category": category,
        "plaque_burden": plaque_burden,
        "modifiers": kept,
        "label": build_label(category, plaque_burden, kept),
        "trace": trace,
    }
