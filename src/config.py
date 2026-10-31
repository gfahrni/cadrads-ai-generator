"""Chargement, fusion avec les valeurs par defaut, validation de settings.json.

Regle du projet : l'humain ne modifie QUE settings.json. Ce module garantit
qu'une erreur de saisie produit un message clair, pas une trace Python.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

LANGUAGES = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}

STYLES = ("free", "structured", "random")
BACKENDS = ("opencode", "http_api", "dry_run")
ENFORCEMENT = ("fail", "warn", "off")

DEFAULTS = {
    "run": {
        "run_id": "auto",
        "n_reports": 10,
        "seed": 42,
        "output_dir": "out",
        "resume": True,
        "max_consecutive_errors": 5,
    },
    "llm": {
        "backend": "opencode",
        "command": ["opencode", "run", "--model", "opencode/grok-code", "{prompt}"],
        "model_label": "opencode",
        "timeout_s": 240,
        "retries": 2,
        "retry_backoff_s": 8,
        "delay_between_calls_s": 1.5,
    },
    "generation": {
        "languages": ["en"],
        "style": "random",
        "age_range": [45, 88],
        "sex": "random",
        "indication": "stable",
        "cadrads_weights": {"0": 0.05, "1": 0.25, "2": 0.25, "3": 0.15, "4A": 0.15, "4B": 0.08, "5": 0.07},
        "modifier_weights": {"S": 0.15, "G": 0.08, "E": 0.05, "V": 0.10, "N": 0.08},
    },
    "other_structures": {
        "describe_normal": False,
        "include_incidental": False,
        "prevalence_scale": 1.0,
        "max_per_case": 3,
        "exclude": [],
    },
    "checks": {
        "label_integrity": "fail",
        "language_match": "warn",
        "modifier_mention": "warn",
        "incidental_mention": "warn",
        "min_words": 35,
        "max_words": 800,
        "forbidden_phrases": ["as an ai", "i cannot", "language model", "```"],
    },
    "export": {
        "filename": "{case_id}_{cadrads}_{lang}",
        "write_csv_index": True,
    },
}


class ConfigError(Exception):
    """Erreur de configuration destinee a l'humain."""


def _strip_doc_keys(node):
    """Retire les cles '_doc' / '_comment' : elles documentent mais ne pilotent rien."""
    if isinstance(node, dict):
        return {k: _strip_doc_keys(v) for k, v in node.items() if not k.startswith("_")}
    if isinstance(node, list):
        return [_strip_doc_keys(v) for v in node]
    return node


def _deep_merge(base, override):
    for key, value in override.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_settings(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Fichier introuvable : {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"JSON invalide dans {path.name} (ligne {exc.lineno}, colonne {exc.colno}) : {exc.msg}"
        ) from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} doit contenir un objet JSON a la racine.")

    cfg = _deep_merge(copy.deepcopy(DEFAULTS), raw)
    errors = validate(cfg)
    if errors:
        raise ConfigError(
            "settings.json invalide :\n" + "\n".join(f"  - {e}" for e in errors)
        )
    return cfg


def validate(cfg: dict) -> list[str]:
    errors: list[str] = []
    run, llm, gen = cfg["run"], cfg["llm"], cfg["generation"]
    checks, export = cfg["checks"], cfg["export"]

    if not isinstance(run["n_reports"], int) or isinstance(run["n_reports"], bool) or run["n_reports"] < 1:
        errors.append("run.n_reports doit etre un entier >= 1")
    if not isinstance(run["seed"], int):
        errors.append("run.seed doit etre un entier (reproductibilite du tirage)")

    if llm["backend"] not in BACKENDS:
        errors.append(f"llm.backend doit valoir {', '.join(BACKENDS)}")
    if llm["backend"] == "opencode":
        cmd = llm.get("command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
            errors.append("llm.command doit etre une liste de chaines non vide")
    if not isinstance(llm["timeout_s"], (int, float)) or llm["timeout_s"] <= 0:
        errors.append("llm.timeout_s doit etre > 0")

    langs = gen["languages"]
    if not isinstance(langs, list) or not langs:
        errors.append("generation.languages doit etre une liste non vide")
    else:
        unknown = [x for x in langs if x not in LANGUAGES]
        if unknown:
            errors.append(f"generation.languages : langue(s) inconnue(s) {unknown} ; valeurs possibles {list(LANGUAGES)}")

    if gen["style"] not in STYLES:
        errors.append(f"generation.style doit valoir {', '.join(STYLES)}")

    age = gen["age_range"]
    if not (isinstance(age, list) and len(age) == 2 and all(isinstance(x, int) for x in age) and 0 < age[0] < age[1] < 120):
        errors.append("generation.age_range doit etre [min, max] avec 0 < min < max < 120")
    if gen["sex"] not in ("random", "male", "female"):
        errors.append("generation.sex doit valoir random, male ou female")
    if gen["indication"] not in ("stable", "acute"):
        errors.append("generation.indication doit valoir stable ou acute")

    weights = gen["cadrads_weights"]
    if not isinstance(weights, dict) or not weights:
        errors.append("generation.cadrads_weights doit etre un objet non vide")
    else:
        bad = [k for k, v in weights.items() if not isinstance(v, (int, float)) or v < 0]
        if bad:
            errors.append(f"generation.cadrads_weights : poids invalides pour {bad}")
        elif sum(weights.values()) <= 0:
            errors.append("generation.cadrads_weights : la somme des poids doit etre > 0")

    for key in ("label_integrity", "language_match", "modifier_mention", "incidental_mention"):
        if checks[key] not in ENFORCEMENT:
            errors.append(f"checks.{key} doit valoir {', '.join(ENFORCEMENT)}")
    other = cfg["other_structures"]
    if not isinstance(other.get("describe_normal"), bool):
        errors.append("other_structures.describe_normal doit etre true ou false")
    if not isinstance(other.get("include_incidental"), bool):
        errors.append("other_structures.include_incidental doit etre true ou false")
    if not isinstance(other.get("prevalence_scale"), (int, float)) or other["prevalence_scale"] < 0:
        errors.append("other_structures.prevalence_scale doit etre un nombre >= 0")
    if not isinstance(other.get("max_per_case"), int) or other["max_per_case"] < 0:
        errors.append("other_structures.max_per_case doit etre un entier >= 0")
    if not isinstance(other.get("exclude"), list):
        errors.append("other_structures.exclude doit etre une liste d'identifiants")

    if checks["min_words"] >= checks["max_words"]:
        errors.append("checks.min_words doit etre < checks.max_words")

    if "{case_id}" not in export["filename"]:
        errors.append("export.filename doit contenir {case_id} (unicite des fichiers)")

    return errors


def set_llm_model(path: Path, model_id: str, stamp: str | None = None) -> dict:
    """Reecrit llm.command (valeur de --model) et llm.model_label dans settings.json.

    Ne touche a rien d'autre. Retourne la config rechargee et validee.
    """
    import re

    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    llm = raw.setdefault("llm", {})

    command = list(llm.get("command") or [])
    if not command:
        command = ["opencode", "run", "--model", model_id, "{prompt}"]
    elif "--model" in command:
        index = command.index("--model")
        if index + 1 < len(command):
            command[index + 1] = model_id
        else:
            command.append(model_id)
    else:
        # insere juste apres le sous-commande "run" si elle existe
        anchor = command.index("run") + 1 if "run" in command else 1
        command[anchor:anchor] = ["--model", model_id]

    if not any("{prompt}" in part for part in command):
        command.append("{prompt}")

    llm["command"] = command
    date = stamp or datetime.now().strftime("%Y-%m-%d")
    llm["model_label"] = f"{model_id}@{date}"

    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return load_settings(path)


def settings_fingerprint(cfg: dict) -> str:
    """Empreinte stable de la config (sans cles de documentation), pour la tracabilite."""
    canonical = json.dumps(_strip_doc_keys(cfg), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def resolve_run_id(cfg: dict) -> str:
    """Identifiant de run.

    'auto' -> horodatage + suffixe aleatoire : deux lancements dans la meme
    seconde ne peuvent pas ecraser le meme dossier. Consequence assumee : avec
    'auto', chaque lancement cree un NOUVEAU dossier, donc `resume` ne s'applique
    pas. Pour reprendre un run interrompu, fixer run.run_id.
    """
    import secrets
    from datetime import datetime

    rid = str(cfg["run"]["run_id"])
    if rid.strip().lower() == "auto":
        return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
    return rid


def is_auto_run_id(cfg: dict) -> bool:
    return str(cfg["run"]["run_id"]).strip().lower() == "auto"
