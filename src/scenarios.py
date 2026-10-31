"""One explicit scenario tree per CAD-RADS category.

Every function below builds the lesions and the modifiers of ONE final category.
Each block states, in plain code:

  - which lesion sets the category (the "index" lesion), and in which vessel;
  - which lesions may be associated, in which vessel and with which grade;
  - which modifiers are compatible, and why the others are not.

Some rules are repeated from block to block. That duplication is deliberate: it
keeps each category readable on its own, instead of hiding the compatibility rules
in generic helpers that must be re-derived every time.

The contract: the scenario returned for target T must produce category T when
passed to classify.classify_category. The planner checks it and flags any drift.
"""
from __future__ import annotations

PLAQUE_TYPES = ["calcified", "mixed", "non-calcified"]

# Stenosis band of each LESION grade (the 4A/4B split is patient-level).
STENOSIS_BAND = {
    "1": (1, 24),
    "2": (25, 49),
    "3": (50, 69),
    "4": (70, 99),
    "5": (100, 100),
}

CORONARY = ("LAD", "LCX", "RCA")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _lesion(rng, reference, vessel, grade):
    """One lesion of the given grade, in the given vessel."""
    low, high = STENOSIS_BAND[grade]
    stenosis = rng.randint(low, high)
    if grade == "5":
        # A chronic total occlusion is calcified or mixed; a purely soft occlusion
        # is atypical and reads as synthetic.
        plaque = rng.choice(["calcified", "mixed"])
    else:
        plaque = rng.choice(PLAQUE_TYPES)
    return {
        "vessel": vessel,
        "segment": rng.choice(reference["vessels"][vessel]),
        "stenosis_pct": stenosis,
        "plaque": plaque,
    }


def _pick_vessel(rng, allowed, used):
    """A vessel from the allowed list, not already carrying a lesion."""
    pool = [v for v in allowed if v not in used]
    return rng.choice(pool) if pool else None


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
def scenario_0(rng, reference, cfg):
    """CAD-RADS 0 — no plaque anywhere.

    Tree: nothing can be associated. Not a single stenosis, no stent, no graft, no
    100 % occlusion. The only compatible modifier is E (a non-atherosclerotic
    abnormality), because it does not imply atherosclerosis.
    """
    return {"lesions": [], "modifiers": _modifiers(rng, ("E",), cfg)}


# --------------------------------------------------------------------------
# CAD-RADS 1
# --------------------------------------------------------------------------
def scenario_1(rng, reference, cfg):
    """CAD-RADS 1 — index lesion 1-24 % in LAD, LCX or RCA.

    Tree:
      - a second grade-1 lesion may be added (never grade 2+: it would raise the
        patient category to 2);
      - left main excluded: any left main lesion >= 50 % forces 4B, and a left main
        plaque under 50 % is not a meaningful CAD-RADS 1 example;
      - S allowed (a patent stent elsewhere), V allowed (a vulnerable plaque is
        often non-obstructive), E allowed;
      - G excluded: a bypass needs a >= 70 % stenosis, impossible here;
      - N excluded: a non-diagnostic segment with no stenosis >= 50 % reclassifies
        the study as CAD-RADS N.
    """
    lesions = [_lesion(rng, reference, _pick_vessel(rng, CORONARY, ()), "1")]
    if rng.random() < 0.30:  # a second minimal lesion is common
        vessel = _pick_vessel(rng, CORONARY, {l["vessel"] for l in lesions})
        if vessel:
            lesions.append(_lesion(rng, reference, vessel, "1"))

    modifiers = _modifiers(rng, ("S", "V", "E"), cfg)
    if "V" in modifiers:
        _soft_plaque(lesions, rng)
    return {"lesions": lesions, "modifiers": modifiers}


# --------------------------------------------------------------------------
# CAD-RADS 2
# --------------------------------------------------------------------------
def scenario_2(rng, reference, cfg):
    """CAD-RADS 2 — index lesion 25-49 %.

    Tree:
      - one or two grade 1-2 lesions may be associated, in other coronary vessels;
        a grade 2 keeps the patient category at 2;
      - left main excluded, for the same reason as in CAD-RADS 1 (>= 50 % forces 4B);
      - S, V, E allowed; G excluded (needs >= 70 %); N excluded (would give N).
    """
    vessels = rng.sample(CORONARY, k=rng.choice([1, 1, 2]))
    lesions = [_lesion(rng, reference, v, "2") for v in vessels]
    if len(lesions) == 1 and rng.random() < 0.35:
        vessel = _pick_vessel(rng, CORONARY, {l["vessel"] for l in lesions})
        if vessel:
            lesions.append(_lesion(rng, reference, vessel, rng.choice(["1", "2"])))

    modifiers = _modifiers(rng, ("S", "V", "E"), cfg)
    if "V" in modifiers:
        _soft_plaque(lesions, rng)
    return {"lesions": lesions, "modifiers": modifiers}


# --------------------------------------------------------------------------
# CAD-RADS 3
# --------------------------------------------------------------------------
def scenario_3(rng, reference, cfg):
    """CAD-RADS 3 — index lesion 50-69 %.

    Tree:
      - associated lesions up to grade 3, so the patient category stays 3;
      - left main excluded: a 50-69 % left main lesion is precisely the case the
        consensus promotes to 4B, so it cannot appear in a CAD-RADS 3 case;
      - N is now compatible: with a >= 50 % stenosis the category survives and N is
        added as a modifier;
      - G still excluded: a bypass needs >= 70 %;
      - S, V, E, N allowed.
    """
    lesions = [_lesion(rng, reference, _pick_vessel(rng, CORONARY, ()), "3")]
    if rng.random() < 0.45:
        vessel = _pick_vessel(rng, CORONARY, {l["vessel"] for l in lesions})
        if vessel:
            lesions.append(_lesion(rng, reference, vessel, rng.choice(["1", "2", "3"])))

    modifiers = _modifiers(rng, ("S", "N", "V", "E"), cfg)
    if "V" in modifiers:
        _soft_plaque(lesions, rng)
    return {"lesions": lesions, "modifiers": modifiers}


# --------------------------------------------------------------------------
# CAD-RADS 4A
# --------------------------------------------------------------------------
def scenario_4a(rng, reference, cfg):
    """CAD-RADS 4A — at least one 70-99 % lesion, in 1 or 2 vessels, no left main.

    Tree:
      - index lesion 70-99 % in LAD, LCX or RCA;
      - a second lesion is allowed (70-99 % in another coronary, or a lower grade);
        two severe vessels still give 4A, three would give 4B, so at most two;
      - left main excluded entirely: >= 50 % would force 4B, and a left main plaque
        under 50 % next to a 4A lesion adds nothing here;
      - S, G, V, E, N allowed: G becomes possible because a >= 70 % stenosis is a
        surgical indication.
    """
    vessels = rng.sample(CORONARY, k=rng.choice([1, 1, 2]))
    lesions = [_lesion(rng, reference, v, "4") for v in vessels]
    if len(lesions) == 1 and rng.random() < 0.35:
        vessel = _pick_vessel(rng, CORONARY, {l["vessel"] for l in lesions})
        if vessel:
            lesions.append(_lesion(rng, reference, vessel, rng.choice(["1", "2", "3"])))

    modifiers = _modifiers(rng, ("S", "G", "N", "V", "E"), cfg)
    if "V" in modifiers:
        _soft_plaque(lesions, rng)
    return {"lesions": lesions, "modifiers": modifiers}


# --------------------------------------------------------------------------
# CAD-RADS 4B
# --------------------------------------------------------------------------
def scenario_4b(rng, reference, cfg):
    """CAD-RADS 4B — either a left main stenosis >= 50 %, or three vessels >= 70 %.

    Tree, two mutually exclusive shapes:
      (a) LEFT MAIN: a left main lesion of grade 3-5 (>= 50 %), optionally with an
          associated coronary lesion of any grade. The left main is what makes it 4B,
          even if its own grade is only 3;
      (b) THREE VESSELS: 70-99 % in LAD, LCX and RCA simultaneously.
    A total occlusion of the left main would be a 5, so shape (a) caps at grade 4.
    S, G, V, E, N allowed.
    """
    if rng.random() < 0.45:
        # (a) left main >= 50 %, capped at 99 % so the case stays a 4B
        lesions = [_lesion(rng, reference, "LM", rng.choice(["3", "4"]))]
        if rng.random() < 0.70:
            lesions.append(_lesion(rng, reference, _pick_vessel(rng, CORONARY, ()),
                                   rng.choice(["1", "2", "3", "4"])))
    else:
        # (b) three-vessel obstructive disease
        lesions = [_lesion(rng, reference, vessel, "4") for vessel in CORONARY]

    modifiers = _modifiers(rng, ("S", "G", "N", "V", "E"), cfg)
    if "V" in modifiers:
        _soft_plaque(lesions, rng)
    return {"lesions": lesions, "modifiers": modifiers}


# --------------------------------------------------------------------------
# CAD-RADS 5
# --------------------------------------------------------------------------
def scenario_5(rng, reference, cfg):
    """CAD-RADS 5 — at least one total occlusion.

    Tree:
      - index lesion 100 % in LAD, LCX or RCA (left main excluded: an occluded left
        main is a different clinical entity, and the consensus example set does not
        cover it);
      - an associated lesion of any grade may coexist, including a second occlusion,
        which does not change the category;
      - V excluded: a vulnerable plaque cannot be described on a totally occluded
        segment, so the model could not honour it;
      - S, G, E, N allowed.
    """
    lesions = [_lesion(rng, reference, _pick_vessel(rng, CORONARY, ()), "5")]
    if rng.random() < 0.55:
        vessel = _pick_vessel(rng, CORONARY, {l["vessel"] for l in lesions})
        if vessel:
            lesions.append(_lesion(rng, reference, vessel, rng.choice(["1", "2", "3", "4", "5"])))

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


def build(rng, reference, target, cfg) -> dict:
    """Build the scenario of one final category."""
    if target not in BUILDERS:
        raise ValueError(f"unknown CAD-RADS target: {target}")
    return BUILDERS[target](rng, reference, cfg)
