"""Building the generation plan.

Three responsibilities:
1. Draw the cases reproducibly (seed) and stratified by target category.
2. Turn each case into a complete, consistent CLINICAL SCENARIO.
3. Have the CAD-RADS label COMPUTED by src/classify.py from the lesions.

Contradictions are avoided BY CONSTRUCTION, not filtered afterwards: each attribute
is drawn inside the space left feasible by the previous ones. The dependency order
is deliberate and must be preserved:

    modifiers  -> constrain the plaque composition (V needs a soft plaque)
    lesions    -> determine the extent, hence the plaque burden P
    composition-> determines the calcium score (no calcified plaque, no calcium)
    stent      -> placed on a segment that carries no significant stenosis
    graft      -> placed in a territory that actually has disease

The label is never left to the LLM's judgement: the target category guides the
scenario, then the REAL category is recomputed from the lesions. A divergence is
reported, never silently accepted.

tests/test_plan_invariants.py asserts those rules over a full-sized plan.
"""
from __future__ import annotations

import random

from . import classify as rules
from . import incidental as incidental_lib

CADRADS_ORDER = ["0", "1", "2", "3", "4A", "4B", "5"]

PLAQUE_TYPES = ["calcified", "mixed", "non-calcified"]

# Calcium contributed by ONE lesion, by composition. The Agatston score is the sum
# over the lesions, so it is always explainable by what the prompt describes: a
# purely non-calcified scenario gets no calcium at all.
CALCIUM_BY_PLAQUE = {
    "calcified": (80, 400),
    "mixed": (30, 150),
    "non-calcified": (0, 0),
}

# Calcium also scales with the stenosis: a dense calcified plaque can exist without
# much narrowing, but a score of 400 behind a 5 % stenosis reads as fabricated.
CALCIUM_SEVERITY_FACTOR = ((25, 0.5), (50, 0.75), (200, 1.0))

# Concrete non-atherosclerotic abnormalities for modifier E: the model must not have
# to invent one.
EXCEPTIONS = [
    "anomalous origin of the right coronary artery from the left coronary sinus",
    "myocardial bridge of the mid LAD",
    "coronary artery fistula from the LAD to the pulmonary artery",
    "fusiform aneurysm of the proximal RCA",
]

# A graft needs a bypassed territory that actually has disease.
GRAFT_BY_VESSEL = {
    "LM": "saphenous vein graft to the left main",
    "LAD": "LIMA to LAD",
    "LCX": "saphenous vein graft to LCX",
    "RCA": "RIMA to RCA",
}

# Minimal acquisition context, so that the "Technique" section of a structured
# report does not have to be invented by the model.
TECHNIQUES = [
    "retrospective ECG-gated acquisition, 120 kV, iodinated contrast",
    "prospective ECG-triggered acquisition, 100 kV, iodinated contrast",
    "retrospective ECG-gated acquisition with dose modulation, 110 kV, iodinated contrast",
]


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
    # A chronic total occlusion is calcified or mixed in practice; a purely soft
    # 100 % occlusion is atypical and reads as synthetic.
    plaque = rng.choice(["calcified", "mixed"]) if stenosis_pct >= 100 else rng.choice(PLAQUE_TYPES)
    return {
        "vessel": vessel,
        "segment": _sample_segment(rng, reference, vessel),
        "stenosis_pct": stenosis_pct,
        "plaque": plaque,
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


def _ensure_soft_plaque(rng, lesions, why: str, trace: list):
    """Make at least one lesion non-calcified, when a rule requires it.

    Used for modifier V: a vulnerable plaque is, by definition, a non-calcified
    plaque with low attenuation. Drawing V on a calcified plaque would force the
    model to invent a feature the scenario denies.
    """
    if not lesions or any(l["plaque"] == "non-calcified" for l in lesions):
        return
    victim = rng.choice(lesions)
    victim["plaque"] = "non-calcified"
    trace.append(f"{why}: one lesion switched to non-calcified plaque ({victim['vessel']})")


def _pick_modifiers(rng, target, weights):
    """Draw the modifiers.

    - S, G and V imply atherosclerosis: impossible in CAD-RADS 0.
    - N is excluded for targets 0/1/2: a non-diagnostic segment combined with a
      stenosis < 50 % reclassifies the case as CAD-RADS N (rule 1 of the engine),
      which would shift the requested distribution.
    - G is excluded for targets 0/1/2: a bypass graft needs a stenosis >= 50 % to
      justify it, which those categories cannot contain.
    """
    chosen = []
    for modifier, weight in weights.items():
        if weight <= 0:
            continue
        if target == "0" and modifier != "E":
            continue
        if modifier == "N" and target in ("0", "1", "2"):
            continue
        if modifier == "G" and target in ("0", "1", "2", "3"):
            continue
        if modifier == "V" and target in ("0", "5"):
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


def _free_segment(rng, reference, lesions, non_evaluable):
    """A vessel/segment carrying neither a lesion nor a non-diagnostic artefact.

    A stent placed here is genuinely patent, with no restenosis to explain: the
    prompt can say "patent" without contradicting a stenosis in the same segment.
    """
    used = {(l["vessel"], l["segment"]) for l in lesions}
    for entry in non_evaluable:
        parts = entry.split()
        if len(parts) == 2:
            used.add((parts[0], parts[1]))
    free = [(vessel, segment) for vessel, segments in reference["vessels"].items()
            for segment in segments if (vessel, segment) not in used]
    if not free:
        return None
    vessel, segment = rng.choice(free)
    return f"{vessel} {segment}"


def _calcium_age_factor(age: int) -> float:
    """The same plaque calcifies less in a younger patient."""
    return 0.5 + max(0, age - 45) * 0.022


def _agatston(rng, lesions, age: int) -> int:
    """Calcium explained by the described plaques: the sum over the lesions."""
    total = 0
    for lesion in lesions:
        low, high = CALCIUM_BY_PLAQUE.get(lesion["plaque"], (0, 0))
        if not high:
            continue
        factor = 1.0
        for threshold, value in CALCIUM_SEVERITY_FACTOR:
            if lesion["stenosis_pct"] < threshold:
                factor = value
                break
        total += int(rng.randint(low, high) * factor)
    return int(total * _calcium_age_factor(age))


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
        trace: list = []

        style = gen["style"]
        if style == "random":
            style = rng.choice(["free", "structured"])

        sex = gen["sex"]
        if sex == "random":
            sex = rng.choice(["male", "female"])

        age = rng.randint(age_lo, age_hi)
        language = rng.choice(gen["languages"])
        technique = rng.choice(TECHNIQUES)
        indication = gen["indication"]
        if indication == "random":
            indication = rng.choice(["stable", "acute"])

        # 1. modifiers first: they constrain the plaque composition below
        declared = _pick_modifiers(rng, target, gen["modifier_weights"])

        # 2. lesions, then the composition constraint imposed by V
        lesions = _lesions_for(rng, reference, target)
        if "V" in declared:
            _ensure_soft_plaque(rng, lesions, "vulnerable plaque", trace)

        # 3. non-diagnostic segment, on a vessel without severe disease
        non_evaluable = _non_evaluable(rng, reference, lesions) if "N" in declared else []

        # 4. category and plaque burden (P is a function of the extent)
        category, _ = rules.classify_category(lesions, non_evaluable)
        burden = rules.plaque_burden_for(lesions)
        classified = rules.classify(lesions, declared, non_evaluable, burden)

        if classified["category"] != target:
            classified["trace"].append(
                f"WARNING: target category {target} but computed category "
                f"{classified['category']} — the computed value is kept"
            )

        # 5. calcium explained by the plaque composition
        agatston = _agatston(rng, lesions, age)

        # 5b. V must designate a plaque, E must name an abnormality: otherwise the
        # model has to invent the finding the modifier refers to.
        vulnerable_site = None
        if "V" in classified["modifiers"]:
            soft = [l for l in lesions if l["plaque"] == "non-calcified"]
            if soft:
                chosen = rng.choice(soft)
                vulnerable_site = f"{chosen['vessel']} {chosen['segment']}"
        exception_site = rng.choice(EXCEPTIONS) if "E" in classified["modifiers"] else None

        # 6. stent on a free segment, so "patent" never contradicts a stenosis
        stent_site = None
        if "S" in classified["modifiers"]:
            stent_site = _free_segment(rng, reference, lesions, non_evaluable)

        # 7. graft into a territory that has disease
        graft_site = None
        if "G" in classified["modifiers"]:
            # A bypass graft is indicated for a >= 70 % stenosis (>= 50 % for the left
            # main); a 51 % lesion is not surgical.
            diseased = sorted({l["vessel"] for l in lesions
                               if l["stenosis_pct"] >= (50 if l["vessel"] == "LM" else 70)})
            if diseased:
                graft_site = GRAFT_BY_VESSEL.get(rng.choice(diseased))

        # 8. minor incidental findings (optional)
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
            "declared_modifiers": declared,
            "stent_site": stent_site,
            "graft_site": graft_site,
            "non_evaluable": non_evaluable,
            "agatston": agatston,
            "technique": technique,
            "vulnerable_site": vulnerable_site,
            "exception_site": exception_site,
            "classification_trace": classified["trace"] + trace,
            "incidental": incidental,
            "language": language,
            "style": style,
            "age": age,
            "sex": sex,
            "indication": indication,
        })

    return cases
