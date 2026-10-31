"""Authentification opencode et verification de la connexion.

Ce module ne gere AUCUN identifiant : opencode garde les cles dans
~/.local/share/opencode/auth.json. Ici on se contente de :

  1. lancer les commandes d'authentification d'opencode ;
  2. verifier par un appel reel, minimal, que la chaine fonctionne (probe) ;
  3. memoriser le resultat dans .state/login.json — cache d'affichage, pas un secret.

Aucune cle n'est ecrite, lue ni journalisee par ce module.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

PROBE_PROMPT = "Reply with exactly this single line and nothing else: PROBE-OK"
STATE_RELATIVE = Path(".state") / "login.json"


# --- etat local (cache d'affichage) ---------------------------------------

def state_path(root: Path) -> Path:
    return Path(root) / STATE_RELATIVE


def load_state(root: Path) -> dict | None:
    path = state_path(root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_state(root: Path, payload: dict) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_state(root: Path) -> None:
    path = state_path(root)
    if path.exists():
        path.unlink()


# --- interactions avec le CLI opencode ------------------------------------

def binary(cfg: dict) -> str:
    return str(cfg["llm"]["command"][0])


def available(cfg: dict) -> bool:
    return shutil.which(binary(cfg)) is not None


def run_interactive(args) -> int:
    """Commande interactive : on laisse opencode piloter son propre ecran."""
    try:
        return subprocess.run(args, check=False).returncode
    except OSError as exc:
        print(f"  ! impossible de lancer {args[0]} : {exc}")
        return 127


def capture(args, timeout: float = 60.0) -> tuple:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timeout apres {timeout:.0f}s"
    except OSError as exc:
        return 127, str(exc)


def auth_list(cfg: dict) -> tuple:
    return capture([binary(cfg), "auth", "list"], timeout=30.0)


def models(cfg: dict) -> tuple:
    return capture([binary(cfg), "models"], timeout=120.0)


def auth_login(cfg: dict) -> int:
    return run_interactive([binary(cfg), "auth", "login"])


def auth_logout(cfg: dict) -> int:
    return run_interactive([binary(cfg), "auth", "logout"])


# --- verification reelle ---------------------------------------------------

def _probe_case() -> dict:
    """Cas factice minimal, pour les clients qui attendent un cas complet."""
    return {
        "case_id": "PROBE", "cadrads": "0", "label": "CAD-RADS 0",
        "lesions": [], "modifiers": [], "plaque_burden": None, "non_evaluable": [],
        "age": 50, "sex": "male", "language": "en", "style": "free",
        "incidental": [], "stent_site": None, "graft_site": None,
        "indication": "stable", "target_cadrads": "0", "target_ok": True,
        "declared_modifiers": [], "agatston": 0, "classification_trace": [],
    }


def probe(cfg: dict) -> dict:
    """Un appel reel et minimal : la seule preuve que la chaine fonctionne.

    Critere de succes : exit 0 et sortie non vide. La presence du marqueur
    PROBE-OK est rapportee a part (`marker_found`), comme indicateur de proprete
    de la sortie : un agent peut ajouter du texte, ce n'est pas un echec.
    """
    if cfg["llm"]["backend"] == "dry_run":
        return {"ok": True, "skipped": True, "latency_s": 0.0, "sample": "",
                "error": None, "marker_found": None}

    from . import llm_client

    try:
        client = llm_client.get_client(cfg)
        client.preflight()
    except llm_client.ClientError as exc:
        return {"ok": False, "skipped": False, "latency_s": 0.0, "sample": "",
                "error": str(exc), "marker_found": None}

    started = time.time()
    result = client.generate(PROBE_PROMPT, _probe_case())
    latency = time.time() - started
    text = result.text or ""

    return {
        "ok": result.ok,
        "skipped": False,
        "latency_s": round(latency, 2),
        "marker_found": "PROBE-OK" in text.upper(),
        "sample": text[:300],
        "error": result.error,
    }


def build_state(cfg: dict, probe_result: dict, auth_exit: int | None = None) -> dict:
    """Etat a persister : que des metadonnees, jamais de secret."""
    return {
        "verified_at": datetime.now().isoformat(timespec="seconds"),
        "probe_ok": bool(probe_result.get("ok")),
        "probe_skipped": bool(probe_result.get("skipped")),
        "backend": cfg["llm"]["backend"],
        "model_label": cfg["llm"].get("model_label", ""),
        "latency_s": probe_result.get("latency_s"),
        "marker_found": probe_result.get("marker_found"),
        "error": probe_result.get("error"),
        "sample": (probe_result.get("sample") or "")[:200],
        "opencode_auth_exit": auth_exit,
        "settings_command": list(cfg["llm"]["command"]),
    }
