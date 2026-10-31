#!/usr/bin/env python3
"""opencode connection — run this BEFORE run.py.

    python3 login.py

Shows the connection status, lets you sign in (account or API key), list the
available models, then makes a real test call. run.py refuses to start until that
test has succeeded.

This script stores no credential: opencode keeps them in
~/.local/share/opencode/auth.json. Only the RESULT of the test is remembered, in
.state/login.json (display cache, disposable).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import auth  # noqa: E402
from src import config as cfgmod  # noqa: E402

LINE = "-" * 66
MAX_SHOWN = 30

HINTS = (
    "opencode could not start. Things to check:",
    " - access rights on ~/.local/share/opencode (log, local database)",
    " - restricted shell/environment (sandbox, container, read-only mount)",
    " - incomplete install -> try 'opencode --version' in a terminal",
)


def _trim(text: str, max_lines: int = 6) -> list:
    """CLIs sometimes dump full stack traces: show only the beginning."""
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
    print("  CAD-RADS generator  --  connection")
    print("=" * 66)
    print(f"  backend  : {cfg['llm']['backend']}")
    print(f"  model    : {cfg['llm'].get('model_label', '')}")
    print(f"  command  : {' '.join(cfg['llm']['command'])}")
    print()

    if cfg["llm"]["backend"] == "dry_run":
        print("  backend 'dry_run': no connection needed.")
        print("  run.py --dry-run works without an account.")
    elif not auth.available(cfg):
        print(f"  ! binary not found: {auth.binary(cfg)}")
        print("    -> install opencode, or fix llm.command in settings.json")
    else:
        print("  Connected providers  (opencode auth list)")
        print(LINE)
        code, text = auth.auth_list(cfg)
        if not text:
            print("  (no output)")
        elif code == 0:
            print("  (no provider connected)" if "no" in text.lower()[:40] else "")
            for line in _trim(text, 12):
                print(f"  {line}")
        else:
            print(f"  'opencode auth list' failed (code {code}):")
            for line in _trim(text, 4):
                print(f"  {line}")
            print()
            for hint in HINTS:
                print(f"  {hint}")

    print()
    if state:
        status = "OK" if state.get("probe_ok") else "FAILED"
        print(f"  Last test : {status} — {state.get('verified_at', '?')}")
        if state.get("model_label"):
            print(f"              model {state['model_label']}, latency {state.get('latency_s')}s")
        if not state.get("probe_ok") and state.get("error"):
            print(f"              error: {str(state['error'])[:160]}")
    else:
        print("  Last test : never")
    print()


def action_login(cfg: dict) -> None:
    print()
    print("  Opening 'opencode auth login'.")
    print("  Pick your account, or the provider whose API key you want to paste.")
    print()
    code = auth.auth_login(cfg)
    print()
    if code != 0:
        print(f"  Sign-in interrupted (code {code}). Nothing was validated.")
        return
    print("  Sign-in complete. Verifying with a real call...")
    result = auth.probe(cfg)
    _report_probe(result)
    auth.save_state(ROOT, auth.build_state(cfg, result, auth_exit=code))


def action_models(cfg: dict) -> None:
    print()
    code, text = auth.models(cfg)
    if code != 0:
        print(f"  'opencode models' failed (code {code}):")
        for line in _trim(text, 4):
            print(f"  {line}")
        print()
        for hint in HINTS:
            print(f"  {hint}")
        return

    all_models = [l.strip() for l in text.splitlines() if l.strip()]
    if not all_models:
        print("  No model available: connect a provider first (option 1).")
        return

    shown = all_models[:MAX_SHOWN]
    print(f"  {len(all_models)} models available (provider/model format).")
    print(LINE)
    for number, name in enumerate(shown, start=1):
        print(f"  {number:>2}) {name}")
    if len(all_models) > len(shown):
        print(f"  ({len(all_models) - len(shown)} more, not shown —")
        print("   use 'opencode models | grep pattern' to find them)")
    print(LINE)

    wanted = str(cfg["llm"].get("model_label", "")).split("@")[0].strip()
    if wanted and wanted in all_models:
        print(f"  OK: the settings.json model ({wanted}) is available.")
        return
    if wanted:
        print(f"  WARNING: '{wanted}' does not appear in the list.")

    if not sys.stdin.isatty():
        print("  -> fix llm.command and llm.model_label in settings.json.")
        return

    choice = input("\n  Number to adopt (Enter = change nothing) > ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(shown)):
        print("  Unchanged.")
        return

    model_id = shown[int(choice) - 1]
    if input(f"  Write '{model_id}' to settings.json? [Y/n] ").strip().lower() \
            not in ("", "y", "yes", "o", "oui"):
        print("  Unchanged.")
        return

    try:
        cfgmod.set_llm_model(ROOT / "settings.json", model_id)
    except Exception as exc:  # noqa: BLE001 - never leave the user with a traceback
        print(f"  ! write failed: {exc}")
        return

    print(f"  settings.json updated: --model {model_id}")
    print("  llm.model_label now carries today's date.")
    print("  Option 3 to validate with a real call.")
    return


def _report_probe(result: dict) -> None:
    print()
    if result.get("skipped"):
        print("  Test skipped (dry_run backend).")
        return
    if not result.get("ok"):
        print(f"  Test FAILED: {result.get('error')}")
        print(LINE)
        if "model unavailable" in str(result.get("error", "")).lower():
            print("  This is not a connection problem: your account works, but the")
            print("  model id in settings.json does not exist for this provider.")
            print("  -> option 2 to see the models actually available and pick one.")
            return
        print("  Things to check:")
        print("   - account or key not connected   -> option 1")
        print("   - model missing or misnamed      -> option 2, then settings.json")
        print("   - opencode binary not found      -> check llm.command")
        return

    print(f"  Test SUCCEEDED in {result['latency_s']}s.")
    if result.get("marker_found"):
        print("  Clean output: the model replied exactly what was asked.")
    else:
        print("  Output did not match the marker (the agent may add text).")
        print("  Check by hand:")
        print(LINE)
        for line in (result.get("sample") or "").splitlines()[:8]:
            print(f"  | {line}")


def action_probe(cfg: dict) -> None:
    print()
    print("  One real, minimal call to the configured model...")
    result = auth.probe(cfg)
    _report_probe(result)
    auth.save_state(ROOT, auth.build_state(cfg, result))


def action_logout(cfg: dict) -> None:
    print()
    answer = input("  Confirm sign-out? [y/N] ").strip().lower()
    if answer not in ("y", "yes", "o", "oui"):
        print("  Cancelled.")
        return
    code = auth.auth_logout(cfg)
    auth.clear_state(ROOT)
    print(f"  Signed out (code {code}). Local state cleared.")


def main() -> int:
    cfg = _load_config()
    if cfg is None:
        return 2

    if not sys.stdin.isatty():
        show_state(cfg)
        print("  Non-interactive mode: run this script in a terminal.\n")
        return 0

    actions = {
        "1": ("Sign in / add an API key", action_login),
        "2": ("List the available models", action_models),
        "3": ("Test the configuration (one real call)", action_probe),
        "4": ("Sign out", action_logout),
    }

    while True:
        show_state(cfg)
        for key, (label, _) in actions.items():
            print(f"  {key}) {label}")
        print("  5) Quit")
        try:
            choice = input("\n  choice > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice in ("5", "q", ""):
            print()
            return 0
        if choice not in actions:
            print("\n  Invalid choice.")
            continue

        try:
            actions[choice][1](cfg)
        except KeyboardInterrupt:
            print("\n  Interrupted.")
        except Exception as exc:  # noqa: BLE001 - never leave the user with a traceback
            print(f"\n  ! unexpected error: {exc}")

        state = auth.load_state(ROOT)
        if state and state.get("probe_ok"):
            print()
            print("  Connection validated. You can now run:  python3 run.py")
            input("  [Enter] to continue ")


if __name__ == "__main__":
    sys.exit(main())
