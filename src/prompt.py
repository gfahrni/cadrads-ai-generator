"""Prompt rendering from the templates in prompts/ (editable without touching code)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .config import LANGUAGES

STYLE_INSTRUCTIONS = {
    "free": (
        "Write a continuous narrative report in flowing prose, as free text, "
        "without section headings."
    ),
    "structured": (
        "Write a structured report with these headings, on separate lines: "
        "Indication, Technique, Findings, Impression. "
        "Finish with the CAD-RADS classification line."
    ),
}

INDICATIONS = {
    "stable": "stable chest pain, evaluation for coronary artery disease",
    "acute": "acute chest pain, rule-out of acute coronary syndrome",
}

BURDEN_LEGEND = (
    "P1 = mild plaque burden, P2 = moderate, P3 = severe, P4 = extensive. "
    "P is computed from the total amount of plaque in the coronary tree, not from the "
    "severity of the worst stenosis: one severe lesion still counts as one lesion. "
    "N = non-diagnostic segment, S = stent, G = graft, E = exception, "
    "V = vulnerable plaque."
)


class PromptError(Exception):
    """Missing template or unknown placeholder."""


def load_templates(prompts_dir: Path) -> dict:
    prompts_dir = Path(prompts_dir)
    templates = {}
    for name in ("system", "user"):
        path = prompts_dir / f"{name}.md"
        if not path.exists():
            raise PromptError(f"Template not found: {path}")
        templates[name] = path.read_text(encoding="utf-8")
    return templates


FINDINGS_LAYOUT_STRUCTURED = """\
Fill the Findings section in this order, one item per line, and never merge two
items into the same paragraph:
1. the coronary arteries, ONE LINE PER ARTERY, in the order left main, LAD, LCX,
   RCA. Each line starts with the artery name and a colon ("LAD: ...") and gives
   that artery's plaque and stenosis, or says it is unremarkable. Do not summarise
   the coronary tree in a single paragraph, and do not merge two arteries into one
   sentence.
2. "Coronary calcium (Agatston score): <value>" on its own line, after the arteries.
   Never move it into the Technique section or into an artery line.
3. the stent / graft / non-diagnostic segment on its own line, when the case has one.
4. the other structures of the field of view, in one paragraph, last.
"""

FINDINGS_LAYOUT_STRUCTURED_NO_LESION = """\
Fill the Findings section in this order, one item per line:
1. one sentence stating that there is no coronary plaque and no luminal stenosis in
   any segment, then "The left main, LAD, LCX and RCA are unremarkable." Do not
   describe any stenosis.
2. "Coronary calcium (Agatston score): <value>" on its own line. Never move it into
   the Technique section.
3. the other structures of the field of view, in one paragraph, last."""

FINDINGS_LAYOUT_FREE_WITH_LESIONS = (
    "Describe the findings vessel by vessel in continuous prose, using the stenosis "
    "severity and plaque composition given above; give the Agatston score in the "
    "findings, not in the Technique, and describe the other structures last when the "
    "case has any."
)
FINDINGS_LAYOUT_FREE_NO_LESION = (
    "State explicitly that there is no coronary plaque and no luminal stenosis, and do "
    "not describe any stenosis; give the Agatston score in the findings, not in the "
    "Technique, and describe the other structures last when the case has any."
)


def _findings_instruction(case: dict, style: str) -> str:
    """The Findings skeleton: one labelled line per artery in structured style."""
    if style == "structured":
        return (FINDINGS_LAYOUT_STRUCTURED if case["lesions"]
                else FINDINGS_LAYOUT_STRUCTURED_NO_LESION)
    return (FINDINGS_LAYOUT_FREE_WITH_LESIONS if case["lesions"]
            else FINDINGS_LAYOUT_FREE_NO_LESION)


def _scenario_block(case: dict, reference: dict) -> str:
    if case["lesions"]:
        lines = ["Findings:"]
        for lesion in case["lesions"]:
            lines.append(
                f"  - {lesion['vessel']} {lesion['segment']}: "
                f"{lesion['plaque']} plaque, {lesion['stenosis_pct']}% luminal stenosis"
            )
    else:
        lines = ["Findings: no coronary plaque, no luminal stenosis in any segment."]

    lines.append(f"Coronary calcium (Agatston score): {case['agatston']}")

    if case["stent_site"]:
        lines.append(f"Stent: previously implanted stent in the {case['stent_site']}, patent")
    if case["graft_site"]:
        lines.append(f"Bypass graft: {case['graft_site']}, patent")
    if case.get("exception_site"):
        lines.append(f"Non-atherosclerotic abnormality (modifier E): {case['exception_site']}")
    if case.get("vulnerable_site"):
        lines.append(
            f"High-risk plaque (modifier V): non-calcified plaque with low attenuation "
            f"and positive remodelling in the {case['vulnerable_site']}"
        )
    if case["non_evaluable"]:
        lines.append("Non-diagnostic segments (non-evaluable, motion artefact): "
                     + "; ".join(case["non_evaluable"]))
    return "\n".join(lines)


def _other_structures_block(case: dict, cfg: dict) -> str:
    """The "other structures" section: empty when both options are disabled.

    - describe_normal  -> describe the organs of the field of view as normal
    - include_incidental -> describe the incidental findings drawn for this case
    """
    block = cfg.get("other_structures", {})
    findings = case.get("incidental") or []
    describe_normal = bool(block.get("describe_normal"))

    if findings:
        lines = ["## Other findings in the field of view",
                 "These unrelated findings are present and must be described briefly:",
                 ""]
        lines.extend(f"- {f['label']}" for f in findings)
        lines.append("")
        if describe_normal:
            # "the other structures" reads as "everything except the coronaries",
            # which includes the organ carrying the finding: say "remaining".
            lines.append(
                "Describe the remaining structures of the field of view as normal, "
                "apart from the findings listed above."
            )
        lines.append("Place them last, after the coronary findings and the Agatston line, "
                     "before the impression.")
        lines.append("")
        return "\n".join(lines)

    if describe_normal:
        return (
            "## Other structures in the field of view\n"
            "Describe the following structures as normal, in one short paragraph: "
            "lungs, pleura, mediastinum, thoracic aorta, pericardium.\n"
        )
    return ""


def _extra_rules(case: dict) -> str:
    rules = []
    if case["stent_site"]:
        rules.append(f"- Describe the stent in the {case['stent_site']} in the findings.")
    if case["graft_site"]:
        rules.append(f"- Describe the bypass graft ({case['graft_site']}) in the findings.")
    if case.get("exception_site"):
        rules.append(f"- Describe the non-atherosclerotic abnormality: {case['exception_site']}.")
    if case.get("vulnerable_site"):
        rules.append(
            f"- Describe the high-risk plaque in the {case['vulnerable_site']} "
            f"(low attenuation, positive remodelling)."
        )
    if case["non_evaluable"]:
        rules.append("- State that the listed segment is non-diagnostic (modifier N).")
    return "\n".join(rules)


def render(templates: dict, case: dict, cfg: dict, reference: dict) -> dict:
    values = {
        "scenario_block": _scenario_block(case, reference),
        "other_structures_block": _other_structures_block(case, cfg),
        "label": case["label"],
        "technique": case.get("technique", "ECG-gated acquisition, iodinated contrast"),
        "findings_instruction": _findings_instruction(case, case["style"]),
        "burden_legend": BURDEN_LEGEND,
        "age": str(case["age"]),
        "sex": case["sex"],
        "indication": INDICATIONS[case["indication"]],
        "language_name": LANGUAGES[case["language"]],
        "style_instruction": STYLE_INSTRUCTIONS[case["style"]],
        "extra_rules": _extra_rules(case),
    }

    system = templates["system"]
    user = templates["user"]
    for key, value in values.items():
        token = "{{" + key + "}}"
        user = user.replace(token, value)
        system = system.replace(token, value)

    if "{{" in user or "{{" in system:
        raise PromptError("Unresolved placeholder in the templates (check the {{...}}).")

    full = system.rstrip() + "\n\n---\n\n" + user.strip()
    return {
        "system": system,
        "user": user,
        "prompt": full,
        "prompt_sha": hashlib.sha256(full.encode("utf-8")).hexdigest()[:16],
    }
