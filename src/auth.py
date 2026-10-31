"""opencode authentication and connection verification.

This module handles NO credential: opencode keeps the keys in
~/.local/share/opencode/auth.json. Here we only:

  1. run opencode's own authentication commands;
  2. verify with a real, minimal call that the chain works (probe);
  3. remember the result in .state/login.json — a display cache, not a secret.

No key is ever written, read or logged by this module.
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


# --- local state (display cache) ------------------------------------------

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


# --- interactions with the opencode CLI -----------------------------------

def binary(cfg: dict) -> str:
    return str(cfg["llm"]["command"][0])


def available(cfg: dict) -> bool:
    return shutil.which(binary(cfg)) is not None


def run_interactive(args) -> int:
    """Interactive command: opencode drives its own screen."""
    try:
        return subprocess.run(args, check=False).returncode
    except OSError as exc:
        print(f"  ! cannot launch {args[0]}: {exc}")
        return 127


def capture(args, timeout: float = 60.0) -> tuple:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout:.0f}s"
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


# --- real verification -----------------------------------------------------

def _probe_case() -> dict:
    """Minimal fake case, for the clients that expect a complete case."""
    return {
        "case_id": "PROBE", "cadrads": "0", "label": "CAD-RADS 0",
        "lesions": [], "modifiers": [], "plaque_burden": None, "non_evaluable": [],
        "age": 50, "sex": "male", "language": "en", "style": "free",
        "incidental": [], "stent_site": None, "graft_site": None,
        "indication": "stable", "target_cadrads": "0", "target_ok": True,
        "declared_modifiers": [], "agatston": 0, "classification_trace": [],
    }


def probe(cfg: dict) -> dict:
    """One real, minimal call: the only proof that the chain works.

    Success criterion: exit code 0 and non-empty output. The presence of the
    PROBE-OK marker is reported separately (`marker_found`), as an indicator of
    output cleanliness: an agent may add text, which is not a failure.
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
    """State to persist: metadata only, never a secret."""
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
