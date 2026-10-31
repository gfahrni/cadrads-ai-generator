"""One explicit scenario tree per CAD-RADS category.

Every function below builds the lesions and the modifiers of ONE final category.
Each block states, in plain code:

  - which lesion sets the category (the "index" lesion), and in which vessel;
  - which lesions may be associated, in which vessel and with which grade;
  - which modifiers are compatible, and why the others are not.

Some rules are repeated from block to block. That duplication is deliberate: it
keeps each category readable on its own, instead of hiding the compatibility rules
in generic helpers that must be re-derived every time.

Two settings drive the number of lesions (generation.lesion_mode):

  - "mono"  : exactly one lesion per patient (none for CAD-RADS 0);
  - "multi" : between one and three lesions, ALWAYS on different coronary arteries.
              Several lesions in the same artery are not modelled.

The contract: the scenario returned for target T must produce category T when
passed to classify.classify_category. The planner checks it and flags any drift.
"""
from __future__ import annotations

PLAQUE_TYPES = ["calcified", "mixed", "non-calcified"]

# Stenosis band of each LESION grade (the 4A/4B split is patient-level).
STENOSIS_BAND = {
    # A stenosis below 5 % is a measurement artefact, not a finding: the band starts
    # at 5 so the report never has to state "1 % luminal stenosis".
    "1": (5, 24),
    "2": (25, 49),
    "3": (50, 69),
    "4": (70, 99),
    "5": (100, 100),
}

VESSELS = ("LM", "LAD", "LCX", "RCA")
CORONARY = ("LAD", "LCX", "RCA")

MAX_LESIONS = 3


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _plaque(rng, grade, age):
    """Plaque composition, biased by age.

    Older patients calcify more: a 77-year-old with two severe plaques and an
    Agatston score of zero does not occur. The bias moves from 15 % calcified at 45
    to 60 % at 88, with a constant 30 % mixed share.
    """
    if grade == "5":
        # A chronic total occlusion is calcified or mixed; a purely soft occlusion
        # is atypical and reads as synthetic.
        return rng.choice(["calcified", "mixed"])
    span = max(0.0, min(1.0, (age - 45) / 43))
    p_calcified = 0.15 + 0.45 * span
    p_mixed = 0.30
    draw = rng.random()
    if draw < p_calcified:
        return "calcified"
    if draw < p_calcified + p_mixed:
        return "mixed"
    return "non-calcified"


def _lesion(rng, reference, vessel, grade, age):
    """One lesion of the given grade, in the given vessel."""
    low, high = STENOSIS_BAND[grade]
    stenosis = rng.randint(low, high)
    plaque = _plaque(rng, grade, age)
    return {
        "vessel": vessel,
        "segment": rng.choice(reference["vessels"][vessel]),
        "stenosis_pct": stenosis,
        "plaque": plaque,
    }


def _budget(rng, cfg) -> int:
    """How many lesions this patient may carry.

    mono  -> exactly 1 (CAD-RADS 0 ignores it: no plaque at all)
    multi -> 1, 2 or 3, uniformly
    """
    if cfg["generation"].get("lesion_mode", "multi") == "mono":
        return 1
    return rng.choice([1, 2, 3])


def _free_vessel(rng, pools, used):
    """A vessel from the pools, not already carrying a lesion (distinct arteries)."""
    pool = [v for group in pools for v in group if v not in used]
    return rng.choice(pool) if pool else None


def _fill(rng, reference, lesions, budget, grades, pools, age):
    """Add associated lesions until the budget is reached, on distinct vessels."""
    while len(lesions) < budget:
        used = {l["vessel"] for l in lesions}
        vessel = _free_vessel(rng, pools, used)
        if vessel is None:
            break
        lesions.append(_lesion(rng, reference, vessel, rng.choice(grades), age))
    return lesions


def _modifiers(rng, allowed, cfg):
    """Draw the modifiers allowed by this category, with the configured weights."""
    weights = cfg["generation"]["modifier_weights"]
    chosen = []
    for modifier in allowed:
        weight = weights.get(modifier, 0)
        if weight > 0 and rng.random() < weight:
            chosen.append(modifier)
    return sorted(chosen)


def _soft_plaque(lesions, rng):
    """Modifier V needs a non-calcified plaque to describe."""
    if any(l["plaque"] == "non-calcified" for l in lesions):
        return
    if lesions:
        rng.choice(lesions)["plaque"] = "non-calcified"


# --------------------------------------------------------------------------
# CAD-RADS 0
# --------------------------------------------------------------------------
def scenario_0(rng, reference, cfg, age):
    """CAD-RADS 0 — no plaque anywhere, whatever the lesion mode.

    Tree: nothing can be associated. Not a single stenosis, no stent, no graft, no
    occlusion. The only compatible modifier is E (a non-atherosclerotic abnormality),
    because it does not imply atherosclerosis.
    """
    return {"lesions": [], "modifiers": _modifiers(rng, ("E",), cfg)}


# --------------------------------------------------------------------------
# CAD-RADS 1
# --------------------------------------------------------------------------
def scenario_1(rng, reference, cfg, age):
    """CAD-RADS 1 — index lesion 1-24 % in LAD, LCX or RCA.

    Tree:
      - associated lesions: grade 1 only (a grade 2 would raise the patient category);
      - left main excluded: a left main plaque under 50 % is not a meaningful
        CAD-RADS 1 example, and >= 50 % would force 4B;
      - S allowed (a patent stent elsewhere), V allowed (a vulnerable plaque is often
        non-obstructive), E allowed;
      - G excluded: a bypass needs a >= 70 % stenosis, impossible here;
      - N excluded: a non-diagnostic segment with no stenosis >= 50 % reclassifies
        the study as CAD-RADS N.
    """
    budget = _budget(rng, cfg)
    lesions = [_lesion(rng, reference, rng.choice(CORONARY), "1", age)]
    if budget > 1:
        _fill(rng, reference, lesions, budget, ("1",), (CORONARY,), age)

    modifiers = _modifiers(rng, ("S", "V", "E"), cfg)
    if "V" in modifiers:
        _soft_plaque(lesions, rng)
    return {"lesions": lesions, "modifiers": modifiers}


# --------------------------------------------------------------------------
# CAD-RADS 2
# --------------------------------------------------------------------------
def scenario_2(rng, reference, cfg, age):
    """CAD-RADS 2 — index lesion 25-49 %.

    Tree:
      - associated lesions: grade 1 or 2, in other coronary arteries;
      - left main excluded, as in CAD-RADS 1 (>= 50 % forces 4B);
      - S, V, E allowed; G excluded (needs >= 70 %); N excluded (would give N).
    """
    budget = _budget(rng, cfg)
    lesions = [_lesion(rng, reference, rng.choice(CORONARY), "2", age)]
    if budget > 1:
        _fill(rng, reference, lesions, budget, ("1", "2"), (CORONARY,), age)

    modifiers = _modifiers(rng, ("S", "V", "E"), cfg)
    if "V" in modifiers:
        _soft_plaque(lesions, rng)
    return {"lesions": lesions, "modifiers": modifiers}


# --------------------------------------------------------------------------
# CAD-RADS 3
# --------------------------------------------------------------------------
def scenario_3(rng, reference, cfg, age):
    """CAD-RADS 3 — index lesion 50-69 %.

    Tree:
      - associated lesions: grade 1 to 3, so the patient category stays 3;
      - left main excluded: a 50-69 % left main lesion is precisely the case the
        consensus promotes to 4B, so it cannot appear in a CAD-RADS 3 case;
      - N is now compatible: with a >= 50 % stenosis the category survives and N is
        added as a modifier;
      - G still excluded: a bypass needs >= 70 %;
      - S, V, E, N allowed.
    """
    budget = _budget(rng, cfg)
    lesions = [_lesion(rng, reference, rng.choice(CORONARY), "3", age)]
    if budget > 1:
        _fill(rng, reference, lesions, budget, ("1", "2", "3"), (CORONARY,), age)

    modifiers = _modifiers(rng, ("S", "N", "V", "E"), cfg)
    if "V" in modifiers:
        _soft_plaque(lesions, rng)
    return {"lesions": lesions, "modifiers": modifiers}


# --------------------------------------------------------------------------
# CAD-RADS 4A
# --------------------------------------------------------------------------
def scenario_4a(rng, reference, cfg, age):
    """CAD-RADS 4A — at least one 70-99 % lesion, no left main, fewer than 3 vessels.

    Tree:
      - index lesion 70-99 % in LAD, LCX or RCA;
      - associated lesions: grade 1-3 when the budget allows a third lesion (a third
        severe vessel would make it 4B), grade 1-4 when at most two lesions in total;
      - left main excluded entirely: >= 50 % would force 4B;
      - S, G, V, E, N allowed: G becomes possible because >= 70 % is a surgical
        indication.
    """
    budget = _budget(rng, cfg)
    lesions = [_lesion(rng, reference, rng.choice(CORONARY), "4", age)]
    if budget > 1:
        # With three lesions, the two associated ones must stay below 70 %.
        grades = ("1", "2", "3") if budget >= 3 else ("1", "2", "3", "4")
        _fill(rng, reference, lesions, budget, grades, (CORONARY,), age)

    modifiers = _modifiers(rng, ("S", "G", "N", "V", "E"), cfg)
    if "V" in modifiers:
        _soft_plaque(lesions, rng)
    return {"lesions": lesions, "modifiers": modifiers}


# --------------------------------------------------------------------------
# CAD-RADS 4B
# --------------------------------------------------------------------------
def scenario_4b(rng, reference, cfg, age):
    """CAD-RADS 4B — a left main stenosis >= 50 %, or three vessels >= 70 %.

    Tree, two mutually exclusive shapes:
      (a) LEFT MAIN: a left main lesion of grade 3-4 (>= 50 %, capped at 99 % so the
          case stays a 4B rather than becoming a 5), plus optional coronary lesions;
      (b) THREE VESSELS: 70-99 % in LAD, LCX and RCA simultaneously. This shape needs
          three lesions, so it is impossible in mono mode.

    S, G, V, E, N allowed.
    """
    budget = _budget(rng, cfg)
    three_vessel_possible = budget >= 3

    if three_vessel_possible and rng.random() < 0.55:
        # (b) three-vessel obstructive disease, exactly three lesions
        lesions = [_lesion(rng, reference, vessel, "4", age) for vessel in CORONARY]
    else:
        # (a) left main >= 50 %, plus optional coronary lesions up to the budget
        lesions = [_lesion(rng, reference, "LM", rng.choice(["3", "4"]), age)]
        if budget > 1:
            _fill(rng, reference, lesions, budget, ("1", "2", "3", "4"), (CORONARY,), age)

    modifiers = _modifiers(rng, ("S", "G", "N", "V", "E"), cfg)
    if "V" in modifiers:
        _soft_plaque(lesions, rng)
    return {"lesions": lesions, "modifiers": modifiers}


# --------------------------------------------------------------------------
# CAD-RADS 5
# --------------------------------------------------------------------------
def scenario_5(rng, reference, cfg, age):
    """CAD-RADS 5 — at least one total occlusion.

    Tree:
      - index lesion 100 % in LAD, LCX or RCA (an occluded left main is a different
        clinical entity and is not modelled);
      - associated lesions of any grade, including a second occlusion: the category
        stays 5;
      - V excluded: a vulnerable plaque cannot be described on a totally occluded
        segment, so the model could not honour it;
      - S, G, E, N allowed.
    """
    budget = _budget(rng, cfg)
    lesions = [_lesion(rng, reference, rng.choice(CORONARY), "5", age)]
    if budget > 1:
        _fill(rng, reference, lesions, budget, ("1", "2", "3", "4", "5"), (CORONARY,), age)

    return {"lesions": lesions, "modifiers": _modifiers(rng, ("S", "G", "N", "E"), cfg)}


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
BUILDERS = {
    "0": scenario_0,
    "1": scenario_1,
    "2": scenario_2,
    "3": scenario_3,
    "4A": scenario_4a,
    "4B": scenario_4b,
    "5": scenario_5,
}


def build(rng, reference, target, cfg, age: int) -> dict:
    """Build the scenario of one final category, for a patient of the given age."""
    if target not in BUILDERS:
        raise ValueError(f"unknown CAD-RADS target: {target}")
    return BUILDERS[target](rng, reference, cfg, age)
