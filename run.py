#!/usr/bin/env python3
"""Single entry point.

    python3 login.py              # once: connect and test (see login.py)
    python3 run.py                # generate according to settings.json
    python3 run.py --dry-run      # plumbing only, no LLM call
    python3 run.py --plan-only    # plan + computed labels, no LLM
    python3 run.py --show-prompt  # print the first prompt and exit
    python3 run.py --limit 3      # quick subset

The human only edits settings.json. The rest of this folder is technical.
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
        description="Generate parameterizable synthetic CCTA reports (CAD-RADS).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--settings", default=str(ROOT / "settings.json"), help="path to settings.json")
    parser.add_argument("--limit", type=int, default=None, help="process only the first N cases")
    parser.add_argument("--no-resume", action="store_true", help="ignore the existing manifest")
    parser.add_argument("--no-probe", action="store_true",
                        help="skip the connection check at startup")
    parser.add_argument("--quiet", action="store_true", help="minimal output")

    # Modes with no model call: mutually exclusive. argparse produces the error
    # message when two of them are combined.
    group = parser.add_argument_group("modes with no model call (mutually exclusive)")
    quiet = group.add_mutually_exclusive_group()
    quiet.add_argument("--dry-run", action="store_true",
                       help="full pipeline with a fake client, outputs isolated in *-dryrun")
    quiet.add_argument("--plan-only", action="store_true",
                       help="print the plan and the computed labels, write nothing")
    quiet.add_argument("--show-prompt", action="store_true",
                       help="print the first case prompt and exit")
    quiet.add_argument("--prompt-only", action="store_true",
                       help="write every rendered prompt to prompts/, without calling the model")

    parser.add_argument("--export-prompts", action="store_true",
                        help="during a normal run, also write each prompt to prompts/")
    return parser


def _parse_args(argv=None):
    return _build_parser().parse_args(argv)


def _rel(path) -> str:
    """Path relative to the project when possible, absolute otherwise."""
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
        if key in ("language", "incidental", "unplanned_incidental"):
            # Cases where the check is not verifiable leave the denominator.
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
        "automatic_checks": {k: check_rate(k) for k in ("label", "language", "modifiers",
                                                       "incidental", "unplanned_incidental",
                                                       "length")},
        "note": "'excluded' = rejected by an automatic check (checks.*=fail); "
                "'error' = technical failure of the LLM call.",
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
        print(f"\n[FILES] {exc}\n", file=sys.stderr)
        return 2

    structure_errors = incidental.config_errors(cfg, catalogue)
    if structure_errors:
        print("\n[CONFIG] other_structures:\n" + "\n".join(f"  - {e}" for e in structure_errors) + "\n",
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
        print(f"  plan: {len(cases)} cases   target/computed drift: {drift}")
        print("-" * 78)
        for case in cases:
            flag = "" if case["target_ok"] else "  <-- DRIFT"
            print(f"  {case['case_id']}  target={case['target_cadrads']:<3} "
                  f"computed={case['cadrads']:<3} {case['language']}  {case['style']:<10} "
                  f"{case['label']:<30} {len(case['lesions'])} lesion(s)"
                  + (f"  +{len(case['incidental'])} finding(s)" if case.get("incidental") else "")
                  + flag)
        return 0

    run_id = cfgmod.resolve_run_id(cfg)
    fingerprint = cfgmod.settings_fingerprint(cfg)
    run = store.RunStore(ROOT, cfg, run_id, dry_run=args.dry_run)
    run.init()

    # Provenance guard: a run folder cannot be reused with another configuration,
    # otherwise the snapshot and the manifest become misleading.
    existing = run.existing_fingerprint()
    if existing and existing != fingerprint:
        print(
            f"\n[RUN] The folder {_rel(run.dir)} was already produced with another "
            f"configuration\n      (fingerprint {existing} vs {fingerprint}).\n"
            f"      -> use another run.run_id, or rename/delete that folder.\n",
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
            print(f"  {len(cases)} prompts written to {_rel(run.prompts_dir)}")
            print(f"  index       : {_rel(run.prompt_index_path)} ({indexed} rows)")
            print("  no model call was made.")
            print()
        return 0

    done = set() if args.no_resume or not cfg["run"]["resume"] else run.done_ids()

    try:
        client = llm_client.get_client(cfg)
        client.preflight()
    except llm_client.ClientError as exc:
        print(f"\n[LLM] {exc}\n", file=sys.stderr)
        return 2

    # ---- connection guard --------------------------------------------------
    # login.py validated a real call once; that flag is not trusted blindly: an
    # account can expire between two launches.
    if not args.dry_run and cfg["llm"]["backend"] != "dry_run":
        state = auth.load_state(ROOT)
        if not state or not state.get("probe_ok"):
            print(
                "\n[LOGIN] No validated connection for this project.\n"
                "  -> run first:  python3 login.py\n",
                file=sys.stderr,
            )
            return 2

        # A green light obtained with another config means nothing for this one.
        stale = None
        if state.get("backend") != cfg["llm"]["backend"]:
            stale = "the backend changed"
        elif list(state.get("settings_command") or []) != list(cfg["llm"]["command"]):
            stale = "the llm.command changed"
        if stale:
            print(
                f"\n[LOGIN] The connection was validated with another configuration ({stale}).\n"
                "  -> re-test with:  python3 login.py\n",
                file=sys.stderr,
            )
            return 2
        if not args.no_probe:
            if not args.quiet:
                print("  connection  : testing...", end="", flush=True)
            result = auth.probe(cfg)
            if not result["ok"]:
                print()
                print(
                    f"[LOGIN] The test call failed: {result['error']}\n"
                    "  -> diagnose with:  python3 login.py\n",
                    file=sys.stderr,
                )
                return 2
            if not args.quiet:
                print(f" ok ({result['latency_s']:.1f}s)")

    # ---- header ------------------------------------------------------------
    if not args.quiet:
        planned = Counter(c["cadrads"] for c in cases)
        print()
        print(f"  run         : {run.run_id}")
        print(f"  output      : {_rel(run.dir)}")
        print(f"  backend     : {cfg['llm']['backend']}  ({cfg['llm'].get('model_label', '')})")
        print(f"  cases       : {len(cases)}"
              + (f"  ({len(done)} already done, skipped)" if done else ""))
        print(f"  strata      : " + "  ".join(f"{k}:{v}" for k, v in sorted(planned.items())))
        print(f"  language(s) : {', '.join(cfg['generation']['languages'])}   "
              f"style: {cfg['generation']['style']}")
        if cfg["other_structures"]["include_incidental"]:
            print("  other       : incidental findings ENABLED "
                  f"(x{cfg['other_structures']['prevalence_scale']}, "
                  f"max {cfg['other_structures']['max_per_case']}/case)")
        elif cfg["other_structures"]["describe_normal"]:
            print("  other       : normal structures described")
        if cfgmod.is_auto_run_id(cfg) and cfg["run"]["resume"] and not args.dry_run:
            print("  note        : run_id 'auto' -> a new folder on every launch; "
                  "set run.run_id to resume an interrupted run.")
        print("-" * 78)

    # ---- main loop ---------------------------------------------------------
    started = time.time()
    consecutive_errors = 0
    session_ok = session_excluded = session_error = 0

    for index, case in enumerate(cases, start=1):
        if case["case_id"] in done:
            if not args.quiet:
                print(f"[{index:>3}/{len(cases)}] {case['case_id']}  already done -> skipped")
            continue

        rendered = prompt.render(templates, case, cfg, reference)
        if args.export_prompts:
            # written BEFORE the call: an interrupted run keeps the prompts on disk
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
                print(f"[{index:>3}/{len(cases)}] {case['case_id']}  FAILED  {result.error}")
            if consecutive_errors >= cfg["run"]["max_consecutive_errors"]:
                print(f"\n[STOP] {consecutive_errors} consecutive failures — see {_rel(run.log_path)}",
                      file=sys.stderr)
                break
            continue

        consecutive_errors = 0
        text = validate.clean(result.text)
        chk = validate.check(text, case, cfg, catalogue)
        status = "ok" if chk["ok"] else "excluded"

        if status == "ok":
            path = run.write_report(case, text)
            session_ok += 1
            target = path.name
        else:
            path = run.write_rejected(case, text)
            session_excluded += 1
            target = f"REJECTED -> {path.name}"

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
            extras = chk["results"]["unplanned_incidental"]["detected"]
            flag = "ok  " if status == "ok" else "EXCL"
            print(f"[{index:>3}/{len(cases)}] {case['case_id']}  {case['cadrads']:<3} "
                  f"{case['language']}  {case['style']:<10} {flag} "
                  f"label={'ok' if label_ok else 'KO':<2} {words:>4}w "
                  f"{result.latency_s:5.1f}s  {case['label']}"
                  + (f"  +{len(case['incidental'])}f" if case.get("incidental") else "")
                  + (f"  [extras:{len(extras)}]" if extras else ""))

        if cfg["llm"]["delay_between_calls_s"] > 0 and not args.dry_run:
            time.sleep(float(cfg["llm"]["delay_between_calls_s"]))

    # ---- summary ----------------------------------------------------------
    if args.export_prompts:
        run.write_prompt_index(cases)

    records = run.records()
    summary = _build_summary(cases, records, run.run_id, fingerprint, cfg)
    summary["session"] = {"ok": session_ok, "excluded": session_excluded,
                          "error": session_error, "duration_s": round(time.time() - started, 1)}
    run.write_summary(summary)
    run.write_csv(records)
    descriptor = run.write_dataset_descriptor(cases, records)

    if not args.quiet:
        checks = summary["automatic_checks"]
        print("-" * 78)
        print(f"  planned          : {summary['n_planned']}")
        print(f"  generated (ok)   : {summary['n_ok']}")
        print(f"  excluded         : {summary['n_excluded']}")
        print(f"  errors           : {summary['n_error']}")
        if summary["n_not_done"]:
            print(f"  not processed    : {summary['n_not_done']}")
        print(f"  label present    : {checks['label']['rate']} "
              f"({checks['label']['passed']}/{checks['label']['considered']})")
        print(f"  language match   : {checks['language']['rate']}")
        print(f"  modifiers shown  : {checks['modifiers']['rate']}")
        print(f"  findings shown   : {checks['incidental']['rate']} "
              f"({checks['incidental']['passed']}/{checks['incidental']['considered']})")
        unplanned = checks["unplanned_incidental"]
        print(f"  unplanned extras : {unplanned['considered'] - unplanned['passed']}"
              f"/{unplanned['considered']} report(s) with a finding the plan did not contain")
        if summary["n_target_mismatch"]:
            print(f"  plan drift       : {summary['n_target_mismatch']} cases (see manifest)")
        print(f"  duration         : {round(time.time() - started, 1)}s")
        if descriptor is not None:
            print(f"  dataset      : {_rel(descriptor)}")
        print(f"  -> {_rel(run.dir)}")
        print()

    return 1 if summary["n_error"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] running the same command again will resume where you stopped.\n", file=sys.stderr)
        sys.exit(130)
