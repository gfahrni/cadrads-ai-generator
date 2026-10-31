"""Rendu des prompts a partir des templates de prompts/ (editables sans code)."""
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
    "N = non-diagnostic segment, S = stent, G = graft, E = exception, "
    "V = vulnerable plaque."
)


class PromptError(Exception):
    """Template manquant ou placeholder inconnu."""


def load_templates(prompts_dir: Path) -> dict:
    prompts_dir = Path(prompts_dir)
    templates = {}
    for name in ("system", "user"):
        path = prompts_dir / f"{name}.md"
        if not path.exists():
            raise PromptError(f"Template introuvable : {path}")
        templates[name] = path.read_text(encoding="utf-8")
    return templates


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
    if "E" in case["modifiers"]:
        lines.append("Exception: non-atherosclerotic coronary abnormality present (e.g. anomalous origin)")
    if case["non_evaluable"]:
        lines.append("Non-diagnostic segments (non-evaluable, motion artefact): "
                     + "; ".join(case["non_evaluable"]))
    return "\n".join(lines)


def _other_structures_block(case: dict, cfg: dict) -> str:
    """Section « autres structures » : vide si les deux options sont desactivees.

    - describe_normal  -> decrire les organes du champ de vue comme normaux
    - include_incidental -> decrire les decouvertes fortuites tirees au sort
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
            lines.append("Describe the other structures of the field of view as normal.")
        lines.append("Place them after the coronary findings and before the impression.")
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
    if "E" in case["modifiers"]:
        rules.append("- Describe the non-atherosclerotic abnormality (modifier E).")
    if case["non_evaluable"]:
        rules.append("- State that the listed segment is non-diagnostic (modifier N).")
    if not case["lesions"]:
        rules.append("- State explicitly that there is no plaque and no stenosis.")
    return "\n".join(rules)


def render(templates: dict, case: dict, cfg: dict, reference: dict) -> dict:
    values = {
        "scenario_block": _scenario_block(case, reference),
        "other_structures_block": _other_structures_block(case, cfg),
        "label": case["label"],
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
        raise PromptError("Placeholder non resolu dans les templates (verifier les {{...}}).")

    full = system.rstrip() + "\n\n---\n\n" + user.strip()
    return {
        "system": system,
        "user": user,
        "prompt": full,
        "prompt_sha": hashlib.sha256(full.encode("utf-8")).hexdigest()[:16],
    }
