"""Building the generation plan.

The planner orchestrates, it does not decide the clinical content: the lesions and
the modifiers of each case come from src/scenarios.py, where one explicit block per
CAD-RADS category states what can be associated with what.

What happens here, in this order, and why:

  1. draw the target category, then ask the scenario tree for a compatible lesion set;
  2. add a non-diagnostic segment when N was drawn (on a vessel without a >= 50 %
     stenosis, otherwise the segment would be medically pointless);
  3. compute the label with src/classify.py and CHECK it matches the target;
  4. derive the calcium score from the plaque composition and the patient age, so
     every point of Agatston has a described plaque behind it;
  5. place the stent on a segment carrying no lesion, so "patent" is never
     contradicted by a stenosis in the same segment;
  6. place the graft into a territory with a surgical stenosis (>= 70 %, >= 50 % for
     the left main);
  7. designate the vulnerable plaque (V) and name the abnormality (E).

Every step restricts the next one; nothing is generated freely and filtered later.
"""
from __future__ import annotations

import random

from . import classify as rules
from . import incidental as incidental_lib
from . import scenarios

CADRADS_ORDER = ["0", "1", "2", "3", "4A", "4B", "5"]

# Calcium contributed by ONE lesion, by composition: a purely non-calcified scenario
# therefore gets no calcium at all, and the score is always explainable.
CALCIUM_BY_PLAQUE = {
    "calcified": (80, 400),
    "mixed": (30, 150),
    "non-calcified": (0, 0),
}

# Calcium also scales with the stenosis: a dense calcified plaque can exist without
# much narrowing, but a score of 400 behind a 5 % stenosis reads as fabricated.
CALCIUM_SEVERITY_FACTOR = ((25, 0.5), (50, 0.75), (200, 1.0))

# A graft needs a bypassed territory with a surgical stenosis.
# A graft is described with the conduit AND the segment it is anastomosed to, so the
# model never has to invent the distal bed.
GRAFT_CONDUIT = {
    "LM": "saphenous vein graft",
    "LAD": "LIMA graft",
    "LCX": "saphenous vein graft",
    "RCA": "RIMA graft",
}

# Concrete non-atherosclerotic abnormalities for modifier E: the model must not have
# to invent one.
EXCEPTIONS = [
    "anomalous origin of the right coronary artery from the left coronary sinus",
    "myocardial bridge of the mid LAD",
    "coronary artery fistula from the LAD to the pulmonary artery",
    "fusiform aneurysm of the proximal RCA",
]

# Minimal acquisition context, so the "Technique" section need not be invented.
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


def _distribution(gen: dict) -> dict:
    """Weights actually used, according to generation.cadrads_distribution.

    - "auto"    : the epidemiology-like defaults (settings.json as shipped)
    - "uniform" : the same share for every category listed in cadrads_weights
    - "manual"  : the weights of cadrads_weights, normalised (percentages work)
    """
    weights = {k: float(v) for k, v in gen["cadrads_weights"].items()}
    mode = gen.get("cadrads_distribution", "auto")
    if mode == "uniform":
        return {k: 1.0 for k in weights}
    return weights


def _non_evaluable(rng, reference, lesions):
    """A non-diagnostic segment, on a vessel without a severe lesion."""
    severe = {l["vessel"] for l in lesions if l["stenosis_pct"] >= 50}
    candidates = [v for v in reference["vessels"] if v not in severe]
    if not candidates:
        return []
    vessel = rng.choice(candidates)
    return [f"{vessel} {rng.choice(reference['vessels'][vessel])}"]


def _free_segment(rng, reference, lesions, non_evaluable):
    """A vessel/segment carrying neither a lesion nor a non-diagnostic artefact."""
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

    counts = allocate(run["n_reports"], _distribution(gen))
    order = [c for c in CADRADS_ORDER if counts.get(c, 0) > 0]

    # Interleaved order: avoids blocks of identical cases and a style drift if the
    # model "warms up" over the session.
    queue = []
    for category in order:
        queue.extend([category] * counts[category])
    rng.shuffle(queue)

    age_lo, age_hi = gen["age_range"]
    cases = []

    for index, target in enumerate(queue, start=1):
        # 1. the age comes first: it biases the plaque composition, so it must be
        #    known before the lesions are drawn
        age = rng.randint(age_lo, age_hi)

        # 2. lesion set and modifiers, from the tree of this category
        scenario = scenarios.build(rng, reference, target, cfg, age)
        lesions = scenario["lesions"]
        declared = scenario["modifiers"]

        style = gen["style"]
        if style == "random":
            style = rng.choice(["free", "structured"])

        sex = gen["sex"]
        if sex == "random":
            sex = rng.choice(["male", "female"])

        language = rng.choice(gen["languages"])
        technique = rng.choice(TECHNIQUES)
        indication = gen["indication"]
        if indication == "random":
            indication = rng.choice(["stable", "acute"])

        # 2. non-diagnostic segment, on a vessel without severe disease
        non_evaluable = _non_evaluable(rng, reference, lesions) if "N" in declared else []

        # 3. label, and the drift check against the target
        classified = rules.classify(lesions, declared, non_evaluable)
        if classified["category"] != target:
            classified["trace"].append(
                f"WARNING: target category {target} but computed category "
                f"{classified['category']} — the computed value is kept"
            )

        # 4. calcium explained by the composition and the age
        agatston = _agatston(rng, lesions, age)

        # 5. stent on a free segment, so "patent" never contradicts a stenosis
        stent_site = None
        if "S" in classified["modifiers"]:
            stent_site = _free_segment(rng, reference, lesions, non_evaluable)

        # 6. graft into a territory with a surgical stenosis
        graft_site = None
        if "G" in classified["modifiers"]:
            surgical = [l for l in lesions
                        if l["stenosis_pct"] >= (50 if l["vessel"] == "LM" else 70)]
            if surgical:
                graft_target = rng.choice(surgical)
                conduit = GRAFT_CONDUIT.get(graft_target["vessel"], "bypass graft")
                if graft_target["vessel"] == "LM":
                    graft_site = f"{conduit} to the left main"
                else:
                    graft_site = (f"{conduit} to the {graft_target['segment']} "
                                  f"{graft_target['vessel']}")

        # 7. V designates a soft plaque, E names an abnormality
        vulnerable_site = None
        if "V" in classified["modifiers"]:
            soft = [l for l in lesions if l["plaque"] == "non-calcified"]
            if soft:
                chosen = rng.choice(soft)
                vulnerable_site = f"{chosen['vessel']} {chosen['segment']}"
        exception_site = rng.choice(EXCEPTIONS) if "E" in classified["modifiers"] else None

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
            "per_lesion": classified["per_lesion"],
            "plaque_burden": classified["plaque_burden"],
            "modifiers": classified["modifiers"],
            "declared_modifiers": declared,
            "stent_site": stent_site,
            "graft_site": graft_site,
            "vulnerable_site": vulnerable_site,
            "exception_site": exception_site,
            "non_evaluable": non_evaluable,
            "agatston": agatston,
            "technique": technique,
            "classification_trace": classified["trace"],
            "incidental": incidental,
            "language": language,
            "style": style,
            "age": age,
            "sex": sex,
            "indication": indication,
        })

    return cases
