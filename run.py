#!/usr/bin/env python3
"""Point d'entree unique.

    python3 login.py              # une fois : connexion + test (voir login.py)
    python3 run.py                # generation selon settings.json
    python3 run.py --dry-run      # plomberie seule, aucun appel LLM
    python3 run.py --plan-only    # plan + labels calcules, sans LLM
    python3 run.py --show-prompt  # affiche le premier prompt et sort
    python3 run.py --limit 3      # sous-echantillon rapide

L'humain ne modifie que settings.json. Le reste de ce dossier est technique.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import auth, incidental, llm_client, planner, prompt, store, validate  # noqa: E402
from src import config as cfgmod  # noqa: E402


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Genere des rapports CCTA synthetiques parametrables (CAD-RADS).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--settings", default=str(ROOT / "settings.json"), help="chemin de settings.json")
    parser.add_argument("--limit", type=int, default=None, help="ne traiter que les N premiers cas")
    parser.add_argument("--no-resume", action="store_true", help="ignorer le manifeste existant")
    parser.add_argument("--no-probe", action="store_true",
                        help="ne pas refaire l'appel de verification au demarrage")
    parser.add_argument("--quiet", action="store_true", help="sortie minimale")

    # Modes sans appel au modele : exclusifs entre eux. argparse fournit le
    # message d'erreur si l'on en combine deux.
    group = parser.add_argument_group("modes sans appel au modele (exclusifs)")
    quiet = group.add_mutually_exclusive_group()
    quiet.add_argument("--dry-run", action="store_true",
                       help="pipeline complet avec un client factice, sorties isolees en *-dryrun")
    quiet.add_argument("--plan-only", action="store_true",
                       help="afficher le plan et les labels calcules, sans rien ecrire")
    quiet.add_argument("--show-prompt", action="store_true",
                       help="afficher le prompt du premier cas puis quitter")
    quiet.add_argument("--prompt-only", action="store_true",
                       help="ecrire tous les prompts rendus dans prompts/, sans appeler le modele")

    parser.add_argument("--export-prompts", action="store_true",
                        help="pendant un run normal, ecrire aussi chaque prompt dans prompts/")
    return parser


def _parse_args(argv=None):
    return _build_parser().parse_args(argv)


def _rel(path) -> str:
    """Chemin relatif au projet si possible, absolu sinon (output_dir hors projet)."""
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def _rate(num: int, den: int) -> str:
    return f"{100.0 * num / den:.1f}%" if den else "n/a"


def _build_summary(cases, records, run_id, fingerprint, cfg) -> dict:
    latest = {}
    for rec in records:
        latest[rec["case_id"]] = rec

    statuses = Counter(rec.get("status") for rec in latest.values())
    per_category = {}
    for case in cases:
        cat = case["cadrads"]
        bucket = per_category.setdefault(cat, {"planned": 0, "ok": 0, "excluded": 0, "error": 0, "skipped": 0})
        bucket["planned"] += 1
        rec = latest.get(case["case_id"])
        if rec is None:
            bucket["skipped"] += 1
        elif rec.get("status") in bucket:
            bucket[rec["status"]] += 1

    def check_rate(key):
        considered = [r for r in latest.values() if r.get("status") in ("ok", "excluded")]
        if key in ("language", "incidental"):
            # Les cas ou le controle n'est pas verifiable sortent du denominateur.
            flag = "undetermined" if key == "language" else "skipped"
            considered = [r for r in considered
                          if not r.get("checks", {}).get("results", {}).get(key, {}).get(flag)]
        passed = sum(1 for r in considered if r.get("checks", {}).get("results", {}).get(key, {}).get("ok"))
        return {"passed": passed, "considered": len(considered), "rate": _rate(passed, len(considered))}

    return {
        "run_id": run_id,
        "created": datetime.now().isoformat(timespec="seconds"),
        "settings_fingerprint": fingerprint,
        "backend": cfg["llm"]["backend"],
        "model_label": cfg["llm"].get("model_label", ""),
        "n_planned": len(cases),
        "n_ok": statuses.get("ok", 0),
        "n_excluded": statuses.get("excluded", 0),
        "n_error": statuses.get("error", 0),
        "n_not_done": len(cases) - len(latest),
        "n_target_mismatch": sum(1 for c in cases if not c["target_ok"]),
        "n_with_incidental": sum(1 for c in cases if c.get("incidental")),
        "per_category": dict(sorted(per_category.items())),
        "automatic_checks": {k: check_rate(k) for k in ("label", "language", "modifiers", "incidental", "length")},
        "note": "Rappels : 'excluded' = rejete par un controle automatique (checks.*=fail) ; "
                "'error' = echec technique de l'appel LLM.",
    }


def main(argv=None) -> int:
    args = _parse_args(argv)

    # ---- configuration ----------------------------------------------------
    try:
        cfg = cfgmod.load_settings(Path(args.settings))
    except cfgmod.ConfigError as exc:
        print(f"\n[CONFIG] {exc}\n", file=sys.stderr)
        return 2

    if args.dry_run:
        cfg["llm"]["backend"] = "dry_run"

    try:
        reference = json.loads((ROOT / "reference" / "cadrads_2_0.json").read_text(encoding="utf-8"))
        catalogue = incidental.load(ROOT / "reference" / "incidental_findings.json")
        templates = prompt.load_templates(ROOT / "prompts")
    except (OSError, json.JSONDecodeError, prompt.PromptError, incidental.CatalogueError) as exc:
        print(f"\n[FICHIERS] {exc}\n", file=sys.stderr)
        return 2

    structure_errors = incidental.config_errors(cfg, catalogue)
    if structure_errors:
        print("\n[CONFIG] other_structures :\n" + "\n".join(f"  - {e}" for e in structure_errors) + "\n",
              file=sys.stderr)
        return 2

    cases = planner.build_cases(cfg, reference, catalogue)
    if args.limit:
        cases = cases[: max(0, args.limit)]

    if args.show_prompt:
        rendered = prompt.render(templates, cases[0], cfg, reference)
        print(rendered["prompt"])
        return 0

    if args.plan_only:
        drift = sum(1 for c in cases if not c["target_ok"])
        print()
        print(f"  plan : {len(cases)} cas   derive cible/calcule : {drift}")
        print("-" * 78)
        for case in cases:
            flag = "" if case["target_ok"] else "  <-- DERIVE"
            print(f"  {case['case_id']}  cible={case['target_cadrads']:<3} "
                  f"calcule={case['cadrads']:<3} {case['language']}  {case['style']:<10} "
                  f"{case['label']:<30} {len(case['lesions'])} lesion(s)"
                  + (f"  +{len(case['incidental'])} decouverte(s)" if case.get("incidental") else "")
                  + flag)
        return 0

    run_id = cfgmod.resolve_run_id(cfg)
    fingerprint = cfgmod.settings_fingerprint(cfg)
    run = store.RunStore(ROOT, cfg, run_id, dry_run=args.dry_run)
    run.init()

    # Garde-fou de provenance : un dossier de run ne se reutilise pas avec une
    # autre configuration, sinon le snapshot et le manifeste deviennent mensongers.
    existing = run.existing_fingerprint()
    if existing and existing != fingerprint:
        print(
            f"\n[RUN] Le dossier {_rel(run.dir)} a deja ete produit avec une autre "
            f"configuration\n      (empreinte {existing} vs {fingerprint}).\n"
            f"      -> utilise un autre run.run_id, ou renomme/supprime ce dossier.\n",
            file=sys.stderr,
        )
        return 2
    if not (run.dir / "run.json").exists():
        run.snapshot_settings(cfg, templates, fingerprint)

    if args.prompt_only:
        for case in cases:
            rendered = prompt.render(templates, case, cfg, reference)
            run.write_prompt(case, rendered["prompt"])
        indexed = run.write_prompt_index(cases)
        if not args.quiet:
            print()
            print(f"  {len(cases)} prompts ecrits dans {_rel(run.prompts_dir)}")
            print(f"  index        : {_rel(run.prompt_index_path)} ({indexed} lignes)")
            print("  aucun appel au modele n'a ete effectue.")
            print()
        return 0

    done = set() if args.no_resume or not cfg["run"]["resume"] else run.done_ids()

    try:
        client = llm_client.get_client(cfg)
        client.preflight()
    except llm_client.ClientError as exc:
        print(f"\n[LLM] {exc}\n", file=sys.stderr)
        return 2

    # ---- garde-fou de connexion -------------------------------------------
    # login.py a valide un appel reel une fois ; on ne fait pas confiance a ce
    # drapeau pour autant : un compte peut expirer entre deux lancements.
    if not args.dry_run and cfg["llm"]["backend"] != "dry_run":
        state = auth.load_state(ROOT)
        if not state or not state.get("probe_ok"):
            print(
                "\n[LOGIN] Aucune connexion validee pour ce projet.\n"
                "  -> lance d'abord :  python3 login.py\n",
                file=sys.stderr,
            )
            return 2

        # Un feu vert obtenu avec une autre config ne vaut rien pour celle-ci.
        stale = None
        if state.get("backend") != cfg["llm"]["backend"]:
            stale = "le backend a change"
        elif list(state.get("settings_command") or []) != list(cfg["llm"]["command"]):
            stale = "la commande llm.command a change"
        if stale:
            print(
                f"\n[LOGIN] La connexion a ete validee avec une autre configuration ({stale}).\n"
                "  -> reteste avec :  python3 login.py\n",
                file=sys.stderr,
            )
            return 2
        if not args.no_probe:
            if not args.quiet:
                print("  connexion   : test en cours...", end="", flush=True)
            result = auth.probe(cfg)
            if not result["ok"]:
                print()
                print(
                    f"[LOGIN] Le test d'appel a echoue : {result['error']}\n"
                    "  -> diagnostique avec :  python3 login.py\n",
                    file=sys.stderr,
                )
                return 2
            if not args.quiet:
                print(f" ok ({result['latency_s']:.1f}s)")

    # ---- en-tete ----------------------------------------------------------
    if not args.quiet:
        planned = Counter(c["cadrads"] for c in cases)
        print()
        print(f"  run        : {run.run_id}")
        print(f"  sortie     : {_rel(run.dir)}")
        print(f"  backend    : {cfg['llm']['backend']}  ({cfg['llm'].get('model_label', '')})")
        print(f"  cas        : {len(cases)}"
              + (f"  ({len(done)} deja faits, ignores)" if done else ""))
        print(f"  strates    : " + "  ".join(f"{k}:{v}" for k, v in sorted(planned.items())))
        print(f"  langue(s)  : {', '.join(cfg['generation']['languages'])}   "
              f"style: {cfg['generation']['style']}")
        if cfg["other_structures"]["include_incidental"]:
            print("  autres     : decouvertes fortuites ACTIVEES "
                  f"(x{cfg['other_structures']['prevalence_scale']}, "
                  f"max {cfg['other_structures']['max_per_case']}/cas)")
        elif cfg["other_structures"]["describe_normal"]:
            print("  autres     : structures normales decrites")
        if cfgmod.is_auto_run_id(cfg) and cfg["run"]["resume"] and not args.dry_run:
            print("  note       : run_id 'auto' -> nouveau dossier a chaque lancement ; "
                  "fixe run.run_id pour reprendre un run interrompu.")
        print("-" * 78)

    # ---- boucle -----------------------------------------------------------
    started = time.time()
    consecutive_errors = 0
    session_ok = session_excluded = session_error = 0

    for index, case in enumerate(cases, start=1):
        if case["case_id"] in done:
            if not args.quiet:
                print(f"[{index:>3}/{len(cases)}] {case['case_id']}  deja fait -> ignore")
            continue

        rendered = prompt.render(templates, case, cfg, reference)
        if args.export_prompts:
            # ecrit AVANT l'appel : un run interrompu laisse les prompts sur disque
            run.write_prompt(case, rendered["prompt"])

        result = client.generate(rendered["prompt"], case)

        expected = {
            "cadrads": case["cadrads"],
            "target_cadrads": case["target_cadrads"],
            "label": case["label"],
            "language": case["language"],
            "style": case["style"],
            "age": case["age"],
            "sex": case["sex"],
            "modifiers": case["modifiers"],
            "plaque_burden": case["plaque_burden"],
            "incidental": [f["id"] for f in case.get("incidental") or []],
        }

        if not result.ok:
            session_error += 1
            consecutive_errors += 1
            run.append({
                "case_id": case["case_id"],
                "status": "error",
                "expected": expected,
                "checks": {},
                "meta": {"attempts": result.attempts, "latency_s": round(result.latency_s, 2),
                         "model": result.model, "backend": result.backend,
                         "prompt_sha": rendered["prompt_sha"], "dry_run": args.dry_run,
                         "error": result.error},
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })
            run.log(f"ERROR {case['case_id']}: {result.error}")
            if not args.quiet:
                print(f"[{index:>3}/{len(cases)}] {case['case_id']}  ECHEC  {result.error}")
            if consecutive_errors >= cfg["run"]["max_consecutive_errors"]:
                print(f"\n[ARRET] {consecutive_errors} echecs consecutifs — voir {_rel(run.log_path)}",
                      file=sys.stderr)
                break
            continue

        consecutive_errors = 0
        text = validate.clean(result.text)
        chk = validate.check(text, case, cfg)
        status = "ok" if chk["ok"] else "excluded"

        if status == "ok":
            path = run.write_report(case, text)
            session_ok += 1
            target = path.name
        else:
            path = run.write_rejected(case, text)
            session_excluded += 1
            target = f"EXCLU -> {path.name}"

        run.write_raw(case["case_id"], {
            "prompt": rendered["prompt"],
            "raw_response": result.text,
            "cleaned": text,
            "checks": chk,
            "scenario": {
                "lesions": case["lesions"],
                "modifiers": case["modifiers"],
                "declared_modifiers": case["declared_modifiers"],
                "non_evaluable": case["non_evaluable"],
                "plaque_burden": case["plaque_burden"],
                "agatston": case["agatston"],
                "label": case["label"],
                "classification_trace": case["classification_trace"],
            },
            "meta": {"attempts": result.attempts, "latency_s": result.latency_s,
                     "model": result.model, "backend": result.backend,
                     "prompt_sha": rendered["prompt_sha"], "dry_run": args.dry_run},
        })

        run.append({
            "case_id": case["case_id"],
            "status": status,
            "file": str(path.relative_to(run.dir)),
            "expected": expected,
            "checks": chk,
            "meta": {"attempts": result.attempts, "latency_s": round(result.latency_s, 2),
                     "model": result.model, "backend": result.backend,
                     "prompt_sha": rendered["prompt_sha"], "dry_run": args.dry_run},
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })

        if not args.quiet:
            words = chk["results"]["length"]["words"]
            label_ok = chk["results"]["label"]["ok"]
            flag = "ok  " if status == "ok" else "EXCL"
            print(f"[{index:>3}/{len(cases)}] {case['case_id']}  {case['cadrads']:<3} "
                  f"{case['language']}  {case['style']:<10} {flag} "
                  f"label={'ok' if label_ok else 'KO':<2} {words:>4}mots "
                  f"{result.latency_s:5.1f}s  {case['label']}"
                  + (f"  +{len(case['incidental'])}f" if case.get("incidental") else ""))

        if cfg["llm"]["delay_between_calls_s"] > 0 and not args.dry_run:
            time.sleep(float(cfg["llm"]["delay_between_calls_s"]))

    # ---- resume -----------------------------------------------------------
    if args.export_prompts:
        run.write_prompt_index(cases)

    records = run.records()
    summary = _build_summary(cases, records, run.run_id, fingerprint, cfg)
    summary["session"] = {"ok": session_ok, "excluded": session_excluded,
                          "error": session_error, "duration_s": round(time.time() - started, 1)}
    run.write_summary(summary)
    run.write_csv(records)

    if not args.quiet:
        checks = summary["automatic_checks"]
        print("-" * 78)
        print(f"  planifies     : {summary['n_planned']}")
        print(f"  generes (ok)  : {summary['n_ok']}")
        print(f"  exclus        : {summary['n_excluded']}")
        print(f"  erreurs       : {summary['n_error']}")
        if summary["n_not_done"]:
            print(f"  non traites   : {summary['n_not_done']}")
        print(f"  label present     : {checks['label']['rate']} "
              f"({checks['label']['passed']}/{checks['label']['considered']})")
        print(f"  fidelite langue   : {checks['language']['rate']}")
        print(f"  modif. decrits    : {checks['modifiers']['rate']}")
        print(f"  decouvr. decrites : {checks['incidental']['rate']} "
              f"({checks['incidental']['passed']}/{checks['incidental']['considered']})")
        if summary["n_target_mismatch"]:
            print(f"  derive du plan    : {summary['n_target_mismatch']} cas (voir manifeste)")
        print(f"  duree             : {round(time.time() - started, 1)}s")
        print(f"  -> {_rel(run.dir)}")
        print()

    return 1 if summary["n_error"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[INTERROMPU] relancer la meme commande reprendra ou vous en etiez.\n", file=sys.stderr)
        sys.exit(130)
