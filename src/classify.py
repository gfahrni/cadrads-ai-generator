"""CAD-RADS 2.0 classification engine — deterministic computation.

The label is computed from the lesions, then imposed on the model in the prompt.
Rule of the engine, in one sentence:

    every lesion carries its own CAD-RADS, the patient category is the HIGHEST of
    them, and category 4 is split into 4A/4B at patient level by left main
    involvement or three-vessel disease.

Reference: Cury RC et al., CAD-RADS 2.0, Radiol Cardiothorac Imaging
2022;4(5):e220183. doi:10.1148/ryct.220183
"""
from __future__ import annotations

THREE_VESSELS = ("LAD", "LCX", "RCA")
MODIFIER_ORDER = ("N", "S", "G", "E", "V")

# Ordering used to take the maximum. "N" is handled separately: it is not a
# stenosis grade but the impossibility of grading.
LESION_ORDER = {"0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}

# Weight of one lesion in the burden score: a non-obstructive plaque counts once, an
# obstructive one twice, an occlusion three times. A raw vessel count overstated the
# burden (three plaques at 8 %, 6 % and 20 % were labelled "severe").
BURDEN_WEIGHT = {"non-obstructive": 1, "obstructive": 2, "occlusion": 3}

# Bands of the total score.
BURDEN_BANDS = ((2, "P1"), (4, "P2"), (6, "P3"))


# --------------------------------------------------------------------------
# Per-lesion grading
# --------------------------------------------------------------------------
def lesion_category(stenosis_pct: int) -> str:
    """CAD-RADS grade of ONE lesion, from its stenosis alone.

    1 : 1-24 %      2 : 25-49 %      3 : 50-69 %
    4 : 70-99 %     5 : 100 % (total occlusion)
    0 : no stenosis
    The 4A/4B split does not exist at lesion level: it is a patient-level rule.
    """
    if stenosis_pct >= 100:
        return "5"
    if stenosis_pct >= 70:
        return "4"
    if stenosis_pct >= 50:
        return "3"
    if stenosis_pct >= 25:
        return "2"
    if stenosis_pct >= 1:
        return "1"
    return "0"


def grade_lesions(lesions) -> list:
    """[(lesion, category), ...] — the per-lesion CAD-RADS, for the trace."""
    return [(lesion, lesion_category(int(lesion["stenosis_pct"]))) for lesion in lesions]


# --------------------------------------------------------------------------
# Patient category
# --------------------------------------------------------------------------
def classify_category(lesions, non_evaluable=()) -> tuple:
    """Patient category plus a justification trace.

    The category is the highest lesion grade, except:
      - a non-diagnostic segment with nothing >= 50 % makes the study unclassifiable (N);
      - a left main stenosis >= 50 % forces 4B, whatever its own grade;
      - three vessels >= 70 % forces 4B.
    """
    graded = grade_lesions(lesions)
    trace = [f"{l['vessel']} {l['segment']}: {l['stenosis_pct']}% -> lesion CAD-RADS {c}"
             for l, c in graded]

    highest = max((c for _, c in graded), default="0", key=lambda c: LESION_ORDER[c])
    trace.append(f"highest lesion grade = {highest}")

    # A study that cannot rule out obstructive disease is not classifiable.
    if non_evaluable and LESION_ORDER[highest] < 3:
        trace.append("non-diagnostic segment with no stenosis >= 50% -> N")
        return "N", trace

    left_main = [l for l in lesions if l["vessel"] == "LM" and int(l["stenosis_pct"]) >= 50]
    three_vessel = len({l["vessel"] for l in lesions
                        if l["vessel"] in THREE_VESSELS and int(l["stenosis_pct"]) >= 70}) >= 3

    # Total occlusion wins over everything.
    if highest == "5":
        trace.append("total occlusion -> 5")
        return "5", trace

    # Left main >= 50 % is the special case the consensus singles out.
    if left_main:
        trace.append("left main >= 50% -> 4B")
        return "4B", trace

    if highest == "4":
        if three_vessel:
            trace.append("three vessels >= 70% -> 4B")
            return "4B", trace
        trace.append(">= 70% without left main or three-vessel disease -> 4A")
        return "4A", trace

    trace.append(f"category = highest lesion grade = {highest}")
    return highest, trace


# --------------------------------------------------------------------------
# Plaque burden, modifiers, label
# --------------------------------------------------------------------------
def burden_score(lesions) -> int:
    """Total plaque score: 1 per non-obstructive lesion, 2 per obstructive, 3 per occlusion."""
    score = 0
    for lesion in lesions:
        stenosis = int(lesion["stenosis_pct"])
        if stenosis >= 100:
            score += BURDEN_WEIGHT["occlusion"]
        elif stenosis >= 50:
            score += BURDEN_WEIGHT["obstructive"]
        elif stenosis >= 1:
            score += BURDEN_WEIGHT["non-obstructive"]
    return score


def plaque_burden_for(lesions):
    """P value implied by the amount of plaque: a weighted score, not a vessel count.

    Examples: one 80 % lesion (score 2) -> P1, as in Figure 7 of the consensus;
    three plaques at 8 %, 6 % and 20 % (score 3) -> P2, not "severe"; three
    occlusions (score 9) -> P4. The consensus defines P semi-quantitatively (SIS,
    calcium score); this weighted score is an explicit approximation, documented as
    such.
    """
    score = burden_score(lesions)
    if score <= 0:
        return None
    for ceiling, label in BURDEN_BANDS:
        if score <= ceiling:
            return label
    return "P4"


def plaque_burden_choices(category: str, lesions) -> list:
    value = plaque_burden_for(lesions)
    return [value] if value else []


def filter_modifiers(modifiers, category: str, non_evaluable=()) -> list:
    """Keep only the modifiers that make clinical sense.

    - N is derived from the presence of a non-diagnostic segment (single source of
      truth), and does not apply when the category IS N.
    - S, G, V imply atherosclerosis: impossible in CAD-RADS 0.
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
        plaque_burden = None
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
        "per_lesion": [{"vessel": l["vessel"], "segment": l["segment"],
                        "stenosis_pct": l["stenosis_pct"], "cadrads": c}
                       for l, c in grade_lesions(lesions)],
    }
