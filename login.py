#!/usr/bin/env python3
"""Connexion a opencode — a lancer AVANT run.py.

    python3 login.py

Affiche l'etat de la connexion, permet de se connecter (compte ou cle API),
de lister les modeles, puis teste reellement un appel. run.py refuse de tourner
tant que ce test n'a pas reussi.

Ce script ne stocke aucune cle : opencode garde les identifiants dans
~/.local/share/opencode/auth.json. Seul le RESULTAT du test est memorise, dans
.state/login.json (cache d'affichage, jetable).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import auth  # noqa: E402
from src import config as cfgmod  # noqa: E402

LINE = "-" * 66

HINTS = (
    "opencode n'a pas pu demarrer. Pistes :",
    " - droits d'acces sur ~/.local/share/opencode (journal, base locale)",
    " - shell/environnement restreint (sandbox, conteneur, droits lecture seule)",
    " - installation incomplete -> essayer 'opencode --version' dans un terminal",
)


def _trim(text: str, max_lines: int = 6) -> list:
    """Les CLI renvoient parfois des traces completes : on n'affiche que le debut."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return lines[:max_lines] + (["  ..."] if len(lines) > max_lines else [])


def _load_config():
    try:
        return cfgmod.load_settings(ROOT / "settings.json")
    except cfgmod.ConfigError as exc:
        print(f"\n[CONFIG] {exc}\n", file=sys.stderr)
        return None


def show_state(cfg: dict) -> None:
    state = auth.load_state(ROOT)
    print()
    print("=" * 66)
    print("  CAD-RADS generator  --  connexion")
    print("=" * 66)
    print(f"  backend  : {cfg['llm']['backend']}")
    print(f"  modele   : {cfg['llm'].get('model_label', '')}")
    print(f"  commande : {' '.join(cfg['llm']['command'])}")
    print()

    if cfg["llm"]["backend"] == "dry_run":
        print("  backend 'dry_run' : aucune connexion necessaire.")
        print("  run.py --dry-run fonctionne sans compte.")
    elif not auth.available(cfg):
        print(f"  ! binaire introuvable : {auth.binary(cfg)}")
        print("    -> installe opencode, ou corrige llm.command dans settings.json")
    else:
        print("  Fournisseurs connectes  (opencode auth list)")
        print(LINE)
        code, text = auth.auth_list(cfg)
        if not text:
            print("  (aucune sortie)")
        elif code == 0:
            print("  (aucun fournisseur connecte)" if "no" in text.lower()[:40] else "")
            for line in _trim(text, 12):
                print(f"  {line}")
        else:
            print(f"  Echec de 'opencode auth list' (code {code}) :")
            for line in _trim(text, 4):
                print(f"  {line}")
            print()
            for hint in HINTS:
                print(f"  {hint}")

    print()
    if state:
        status = "OK" if state.get("probe_ok") else "ECHEC"
        print(f"  Dernier test : {status} — {state.get('verified_at', '?')}")
        if state.get("model_label"):
            print(f"                 modele {state['model_label']}, latence {state.get('latency_s')}s")
        if not state.get("probe_ok") and state.get("error"):
            print(f"                 erreur : {str(state['error'])[:160]}")
    else:
        print("  Dernier test : jamais")
    print()


def action_login(cfg: dict) -> None:
    print()
    print("  Ouverture de 'opencode auth login'.")
    print("  Choisis ton compte, ou le fournisseur dont tu veux coller la cle API.")
    print()
    code = auth.auth_login(cfg)
    print()
    if code != 0:
        print(f"  Connexion interrompue (code {code}). Rien n'a ete valide.")
        return
    print("  Connexion terminee. Verification par un appel reel...")
    result = auth.probe(cfg)
    _report_probe(result)
    auth.save_state(ROOT, auth.build_state(cfg, result, auth_exit=code))


MAX_AFFICHES = 30


def action_models(cfg: dict) -> None:
    print()
    code, text = auth.models(cfg)
    if code != 0:
        print(f"  Echec de 'opencode models' (code {code}) :")
        for line in _trim(text, 4):
            print(f"  {line}")
        print()
        for hint in HINTS:
            print(f"  {hint}")
        return

    toutes = [l.strip() for l in text.splitlines() if l.strip()]
    if not toutes:
        print("  Aucun modele disponible : connecte d'abord un fournisseur (option 1).")
        return

    affichees = toutes[:MAX_AFFICHES]
    print(f"  {len(toutes)} modeles disponibles (format provider/modele).")
    print(LINE)
    for numero, nom in enumerate(affichees, start=1):
        print(f"  {numero:>2}) {nom}")
    if len(toutes) > len(affichees):
        print(f"  ({len(toutes) - len(affichees)} de plus, non affiches —")
        print("   utilise 'opencode models | grep motif' pour les retrouver)")
    print(LINE)

    wanted = str(cfg["llm"].get("model_label", "")).split("@")[0].strip()
    if wanted and wanted in toutes:
        print(f"  OK : le modele de settings.json ({wanted}) est disponible.")
        return
    if wanted:
        print(f"  ATTENTION : '{wanted}' n'apparait pas dans la liste.")

    if not sys.stdin.isatty():
        print("  -> corrige llm.command et llm.model_label dans settings.json.")
        return

    choix = input("\n  Numero a adopter (Entree = ne rien changer) > ").strip()
    if not choix.isdigit() or not (1 <= int(choix) <= len(affichees)):
        print("  Inchange.")
        return

    model_id = affichees[int(choix) - 1]
    if input(f"  Ecrire '{model_id}' dans settings.json ? [O/n] ").strip().lower() \
            not in ("", "o", "oui", "y", "yes"):
        print("  Inchange.")
        return

    try:
        cfgmod.set_llm_model(ROOT / "settings.json", model_id)
    except Exception as exc:  # noqa: BLE001 - on ne laisse pas l'utilisateur sur une trace
        print(f"  ! echec de l'ecriture : {exc}")
        return

    print(f"  settings.json mis a jour : --model {model_id}")
    print("  llm.model_label porte desormais la date du jour.")
    print("  Option 3 pour valider par un appel reel.")
    return


def _report_probe(result: dict) -> None:  # noqa: C901
    print()
    if result.get("skipped"):
        print("  Test ignore (backend dry_run).")
        return
    if not result.get("ok"):
        print(f"  ECHEC du test : {result.get('error')}")
        print(LINE)
        if "model unavailable" in str(result.get("error", "")).lower():
            print("  Ce n'est pas un probleme de connexion : ton compte fonctionne, mais")
            print("  l'identifiant de modele de settings.json n'existe pas chez ce fournisseur.")
            print("  -> option 2 pour voir les modeles reellement disponibles et en choisir un.")
            return
        print("  Pistes :")
        print("   - compte ou cle non connecte   -> option 1")
        print("   - modele absent ou mal nomme   -> option 2, puis settings.json")
        print("   - binaire opencode introuvable -> verifier llm.command")
        return

    print(f"  Test REUSSI en {result['latency_s']}s.")
    if result.get("marker_found"):
        print("  Sortie propre : le modele a repondu exactement ce qui etait demande.")
    else:
        print("  Sortie non conforme au marqueur (l'agent ajoute peut-etre du texte).")
        print("  A verifier a la main :")
        print(LINE)
        for line in (result.get("sample") or "").splitlines()[:8]:
            print(f"  | {line}")


def action_probe(cfg: dict) -> None:
    print()
    print("  Un appel reel, minimal, vers le modele configure...")
    result = auth.probe(cfg)
    _report_probe(result)
    auth.save_state(ROOT, auth.build_state(cfg, result))


def action_logout(cfg: dict) -> None:
    print()
    answer = input("  Confirmer la deconnexion ? [o/N] ").strip().lower()
    if answer not in ("o", "oui", "y", "yes"):
        print("  Annule.")
        return
    code = auth.auth_logout(cfg)
    auth.clear_state(ROOT)
    print(f"  Deconnexion effectuee (code {code}). Etat local efface.")


def main() -> int:
    cfg = _load_config()
    if cfg is None:
        return 2

    if not sys.stdin.isatty():
        show_state(cfg)
        print("  Mode non interactif : lance ce script dans un terminal.\n")
        return 0

    actions = {
        "1": ("Se connecter / ajouter une cle API", action_login),
        "2": ("Lister les modeles disponibles", action_models),
        "3": ("Tester la configuration (1 appel reel)", action_probe),
        "4": ("Se deconnecter", action_logout),
    }

    while True:
        show_state(cfg)
        for key, (label, _) in actions.items():
            print(f"  {key}) {label}")
        print("  5) Quitter")
        try:
            choice = input("\n  choix > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice in ("5", "q", ""):
            print()
            return 0
        if choice not in actions:
            print("\n  Choix invalide.")
            continue

        try:
            actions[choice][1](cfg)
        except KeyboardInterrupt:
            print("\n  Interrompu.")
        except Exception as exc:  # noqa: BLE001 - on ne laisse pas l'utilisateur sur une trace
            print(f"\n  ! erreur inattendue : {exc}")

        state = auth.load_state(ROOT)
        if state and state.get("probe_ok"):
            print()
            print("  Connexion validee. Tu peux lancer :  python3 run.py")
            input("  [Entree] pour continuer ")


if __name__ == "__main__":
    sys.exit(main())
