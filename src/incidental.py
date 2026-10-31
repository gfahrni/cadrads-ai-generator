"""Decouvertes fortuites mineures, a prevalence realiste.

Deux notions distinctes, qu'il ne faut pas confondre :

- `other_structures.describe_normal` : le rapport decrit les structures normales
  du champ de vue (poumons, plevre, mediastin, aorte, pericarde). C'est une
  question de FORME du rapport, pas de pathologie.
- `other_structures.include_incidental` : on ajoute des anomalies MINEURES
  tirees a leur prevalence approximative, pour ressembler a une vraie cohorte.

Tout est pilote par reference/incidental_findings.json : rien n'est code en dur
ici, et modifier le catalogue suffit a changer le comportement.

Les prevalences sont des ordres de grandeur a verifier avant publication : ce
n'est pas un modele epidemiologique.
"""
from __future__ import annotations

import json
from pathlib import Path


class CatalogueError(Exception):
    """Catalogue illisible ou incoherent."""


def load(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise CatalogueError(f"Catalogue introuvable : {path}")
    try:
        catalogue = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogueError(
            f"Catalogue JSON invalide (ligne {exc.lineno}, colonne {exc.colno}) : {exc.msg}"
        ) from exc

    if "findings" not in catalogue or "organs" not in catalogue:
        raise CatalogueError("Le catalogue doit contenir 'findings' et 'organs'.")
    for finding in catalogue["findings"]:
        missing = {"id", "organ", "label_en", "prevalence"} - set(finding)
        if missing:
            raise CatalogueError(f"Decouverte '{finding.get('id', '?')}' : champs manquants {sorted(missing)}")
        if finding["organ"] not in catalogue["organs"]:
            raise CatalogueError(
                f"Decouverte '{finding['id']}' : organe '{finding['organ']}' absent de la table 'organs'."
            )
    return catalogue


def config_errors(cfg: dict, catalogue: dict) -> list:
    """Valide les identifiants cites dans settings.json contre le catalogue."""
    block = cfg["other_structures"]
    errors = []
    known = {f["id"] for f in catalogue["findings"]}
    unknown = [x for x in block.get("exclude", []) if x not in known]
    if unknown:
        errors.append(
            f"other_structures.exclude : identifiant(s) inconnu(s) {unknown} ; "
            f"valeurs possibles : {sorted(known)}"
        )
    if block.get("max_per_case", 0) < 0:
        errors.append("other_structures.max_per_case doit etre >= 0")
    if block.get("prevalence_scale", 1.0) < 0:
        errors.append("other_structures.prevalence_scale doit etre >= 0")
    return errors


def keywords_for(finding: dict, organ: str, language: str, catalogue: dict) -> list:
    """Mots-cles de detection dans la langue du rapport (liste vide si inconnue)."""
    return list(catalogue["organs"].get(organ, {}).get(language, []))


def sample(rng, age: int, sex: str, cfg: dict, catalogue: dict) -> list:
    """Tire les decouvertes fortuites d'un cas, de facon deterministe.

    Regles, volontairement simples :
      - prevalence du catalogue x prevalence_scale ;
      - ignoree si age < age_min ;
      - ignoree si sex restreint et different ;
      - au plus max_per_case decouvertes par patient.
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
