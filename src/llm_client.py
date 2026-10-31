"""LLM client.

WARNING: the CALL part is a PLACEHOLDER.
  - backend "dry_run"  -> no request, fabricated text (plumbing testable)
  - backend "opencode" -> runs the command defined in settings.json > llm.command
  - backend "http_api" -> NOT IMPLEMENTED (see HttpApiClient below)

Nothing has been tested end to end: the exact opencode command must be verified on
the real account, then adjusted in settings.json (not in this file).
"""
from __future__ import annotations

import dataclasses
import shutil
import subprocess
import time


# Error markers that will not resolve by retrying (wrong model id, bad key, ...).
PERMANENT_ERRORS = (
    "model unavailable",
    "not found",
    "unauthorized",
    "invalid api key",
    "authentication",
    "no such model",
)


class ClientError(Exception):
    """Fatal error (configuration, missing binary) — immediate stop."""


@dataclasses.dataclass
class LLMResult:
    text: str
    error: str | None
    attempts: int
    latency_s: float
    backend: str
    model: str

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


# --------------------------------------------------------------------------
# dry_run
# --------------------------------------------------------------------------
class DryRunClient:
    """Produces fabricated text that is nonetheless structurally realistic.

    Purpose: validate the plan, the prompts, the manifest, the resume logic and the
    automatic checks without spending a single call. The text has no scientific
    value; outputs are isolated in a '-dryrun' suffixed run_id and the manifest
    carries dry_run=true.
    """

    def __init__(self, cfg: dict):
        self.model = "dry-run"

    def preflight(self) -> None:
        return None

    def generate(self, prompt: str, case: dict) -> LLMResult:
        started = time.time()
        body = [f"Indication: evaluation of coronary artery disease in a {case['age']}-year-old patient."]
        body.append("Technique: ECG-gated coronary CT angiography, 0.5 mm slices, no intravenous contrast reaction.")
        if case["lesions"]:
            findings = "; ".join(
                f"{l['vessel']} {l['segment']} with {l['plaque']} plaque and {l['stenosis_pct']}% luminal stenosis"
                for l in case["lesions"]
            )
            body.append(f"Findings: {findings}.")
        else:
            body.append("Findings: no coronary plaque and no luminal stenosis.")
        if case.get("stent_site"):
            body.append(f"A patent stent is present in the {case['stent_site']}.")
        if case.get("graft_site"):
            body.append(f"A patent bypass graft is present ({case['graft_site']}).")
        if case["non_evaluable"]:
            body.append("Non-diagnostic segments: " + ", ".join(case["non_evaluable"]) + " (motion artefact).")
        if "E" in case["modifiers"]:
            body.append("An anomalous coronary origin is noted.")
        if "V" in case["modifiers"]:
            body.append("A vulnerable high-risk plaque is suspected.")
        if case["plaque_burden"]:
            body.append(f"Overall coronary plaque burden: {case['plaque_burden']}.")
        for finding in case.get("incidental") or []:
            body.append(f"Other finding: {finding['label']}.")
        body.append("Impression: findings as described above.")
        body.append(case["label"])
        text = "\n".join(body)
        return LLMResult(text, None, 1, 0.0, "dry_run", self.model)


# --------------------------------------------------------------------------
# opencode (CLI)
# --------------------------------------------------------------------------
class OpencodeClient:
    """Calls the opencode CLI through a command template.

    Two modes, chosen automatically:
      - if any element of llm.command contains {prompt} -> prompt passed as argument
      - otherwise                                      -> prompt sent on stdin
    The stdin mode is preferable for long prompts (ARG_MAX limit).
    """

    def __init__(self, cfg: dict):
        llm = cfg["llm"]
        self.command = list(llm["command"])
        self.model = llm.get("model_label", "opencode")
        self.timeout = float(llm["timeout_s"])
        self.retries = int(llm["retries"])
        self.backoff = float(llm["retry_backoff_s"])

    def preflight(self) -> None:
        exe = self.command[0]
        if shutil.which(exe) is None:
            raise ClientError(
                f"Command not found: '{exe}'.\n"
                f"  -> install/open opencode, or fix llm.command in settings.json.\n"
                f"  -> to test the plumbing without any API: python3 run.py --dry-run"
            )

    def _argv(self, prompt: str):
        if any("{prompt}" in part for part in self.command):
            return [part.replace("{prompt}", prompt) for part in self.command]
        return None

    def generate(self, prompt: str, case: dict) -> LLMResult:
        last_error = "unknown error"
        attempts_made = 0
        started = time.time()
        for attempt in range(1, self.retries + 2):
            attempts_made = attempt
            try:
                argv = self._argv(prompt)
                if argv is None:
                    proc = subprocess.run(
                        self.command, input=prompt, capture_output=True,
                        text=True, timeout=self.timeout,
                    )
                else:
                    proc = subprocess.run(
                        argv, capture_output=True, text=True, timeout=self.timeout,
                    )
                if proc.returncode != 0:
                    raise RuntimeError(f"exit code {proc.returncode}: {(proc.stderr or '').strip()[:300]}")
                text = (proc.stdout or "").strip()
                if not text:
                    raise RuntimeError("empty output")
                return LLMResult(text, None, attempt, time.time() - started, "opencode", self.model)
            except subprocess.TimeoutExpired:
                last_error = f"timeout after {self.timeout:.0f}s"
            except Exception as exc:  # noqa: BLE001 - everything is logged
                last_error = str(exc)
            # Deterministic errors will not become true by retrying: fail fast.
            if any(marker in last_error.lower() for marker in PERMANENT_ERRORS):
                break
            if attempt <= self.retries:
                time.sleep(self.backoff * attempt)
        return LLMResult("", last_error, attempts_made, time.time() - started, "opencode", self.model)


# --------------------------------------------------------------------------
# http_api: PLACEHOLDER
# --------------------------------------------------------------------------
class HttpApiClient:
    """PLACEHOLDER — intentionally not implemented.

    If you prefer a direct HTTP call (API key) over the opencode CLI:
      1. write the POST here (the stdlib 'urllib.request' module is enough,
         nothing needs to be installed);
      2. convert system+user into messages [{role, content}];
      3. return LLMResult(text, ...) like the other clients;
      4. set llm.backend to "http_api" in settings.json.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def preflight(self) -> None:
        raise ClientError(
            "backend 'http_api' is not implemented yet (placeholder in src/llm_client.py).\n"
            "  -> use llm.backend = 'opencode' or 'dry_run'."
        )

    def generate(self, prompt: str, case: dict) -> LLMResult:
        raise ClientError("HttpApiClient is not implemented (placeholder).")


def get_client(cfg: dict):
    backend = cfg["llm"]["backend"]
    if backend == "dry_run":
        return DryRunClient(cfg)
    if backend == "opencode":
        return OpencodeClient(cfg)
    if backend == "http_api":
        return HttpApiClient(cfg)
    raise ClientError(f"unknown backend: {backend}")
