"""Minor incidental findings, drawn at a realistic prevalence.

Two distinct notions, not to be confused:

- `other_structures.describe_normal`: the report describes the normal structures
  of the field of view (lungs, pleura, mediastinum, aorta, pericardium). That is a
  question of report SHAPE, not of pathology.
- `other_structures.include_incidental`: minor abnormalities are added, drawn at
  their approximate prevalence, so the corpus resembles a real cohort.

Everything is driven by reference/incidental_findings.json: nothing is hard-coded
here, and editing the catalogue is enough to change the behaviour.

The prevalences are orders of magnitude to be reviewed before publication: this is
not an epidemiological model.
"""
from __future__ import annotations

import json
from pathlib import Path


class CatalogueError(Exception):
    """Unreadable or inconsistent catalogue."""


def load(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise CatalogueError(f"Catalogue not found: {path}")
    try:
        catalogue = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogueError(
            f"Invalid catalogue JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        ) from exc

    if "findings" not in catalogue or "organs" not in catalogue:
        raise CatalogueError("The catalogue must contain 'findings' and 'organs'.")
    for finding in catalogue["findings"]:
        missing = {"id", "organ", "label_en", "prevalence"} - set(finding)
        if missing:
            raise CatalogueError(f"Finding '{finding.get('id', '?')}': missing keys {sorted(missing)}")
        if finding["organ"] not in catalogue["organs"]:
            raise CatalogueError(
                f"Finding '{finding['id']}': organ '{finding['organ']}' is absent from the 'organs' table."
            )
    return catalogue


def config_errors(cfg: dict, catalogue: dict) -> list:
    """Validate the identifiers referenced from settings.json against the catalogue."""
    block = cfg["other_structures"]
    errors = []
    known = {f["id"] for f in catalogue["findings"]}
    unknown = [x for x in block.get("exclude", []) if x not in known]
    if unknown:
        errors.append(
            f"other_structures.exclude: unknown identifier(s) {unknown}; "
            f"possible values: {sorted(known)}"
        )
    if block.get("max_per_case", 0) < 0:
        errors.append("other_structures.max_per_case must be >= 0")
    if block.get("prevalence_scale", 1.0) < 0:
        errors.append("other_structures.prevalence_scale must be >= 0")
    return errors


def keywords_for(finding: dict, organ: str, language: str, catalogue: dict) -> list:
    """Detection keywords in the report language (empty list if unknown)."""
    return list(catalogue["organs"].get(organ, {}).get(language, []))


def sample(rng, age: int, sex: str, cfg: dict, catalogue: dict) -> list:
    """Draw the incidental findings of one case, deterministically.

    Deliberately simple rules:
      - catalogue prevalence x prevalence_scale;
      - skipped if age < age_min;
      - skipped if restricted to a sex and it differs;
      - at most max_per_case findings per patient.
    """
    block = cfg["other_structures"]
    if not block.get("include_incidental"):
        return []

    scale = float(block.get("prevalence_scale", 1.0))
    excluded = set(block.get("exclude", []))

    chosen = []
    for finding in catalogue["findings"]:
        if finding["id"] in excluded:
            continue
        if age < finding.get("age_min", 0):
            continue
        sex_rule = finding.get("sex", "any")
        if sex_rule != "any" and sex_rule != sex:
            continue
        probability = min(1.0, float(finding["prevalence"]) * scale)
        if probability > 0 and rng.random() < probability:
            chosen.append(finding)

    limit = int(block.get("max_per_case", 2))
    if len(chosen) > limit:
        chosen = rng.sample(chosen, k=limit)
        chosen.sort(key=lambda f: f["id"])

    return chosen
