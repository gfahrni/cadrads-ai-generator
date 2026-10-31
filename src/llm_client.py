"""Client LLM.

ATTENTION : la partie APPEL est un PLACEHOLDER.
  - backend "dry_run"  -> aucune requete, texte factice (plomberie testable)
  - backend "opencode" -> lance la commande definie dans settings.json > llm.command
  - backend "http_api" -> NON IMPLEMENTE (voir HttpApiClient plus bas)

Rien n'a ete teste de bout en bout : la commande exacte d'opencode doit etre
verifiee sur le compte reel, puis ajustee dans settings.json (pas dans ce fichier).
"""
from __future__ import annotations

import dataclasses
import shutil
import subprocess
import time


class ClientError(Exception):
    """Erreur fatale (configuration, binaire absent) — arret immediat."""


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
    """Genere un texte factice MAIS structurellement realiste.

    Objectif : valider le plan, les prompts, le manifeste, la reprise et les
    controles automatiques sans consommer un seul appel. Le texte n'a aucune
    valeur scientifique ; les sorties sont isolees dans un run_id suffixe
    '-dryrun' et le manifeste porte dry_run=true.
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
    """Appelle le CLI opencode via un template de commande.

    Deux modes, choisis automatiquement :
      - si un element de llm.command contient {prompt} -> prompt passe en argument
      - sinon                                        -> prompt envoye sur stdin
    Le mode stdin est preferable pour les prompts longs (limite ARG_MAX).
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
                f"Commande introuvable : '{exe}'.\n"
                f"  -> installer/ouvrir opencode, ou corriger llm.command dans settings.json.\n"
                f"  -> pour tester la plomberie sans API : python3 run.py --dry-run"
            )

    def _argv(self, prompt: str):
        if any("{prompt}" in part for part in self.command):
            return [part.replace("{prompt}", prompt) for part in self.command]
        return None

    def generate(self, prompt: str, case: dict) -> LLMResult:
        last_error = "erreur inconnue"
        started = time.time()
        for attempt in range(1, self.retries + 2):
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
                    raise RuntimeError(f"code de sortie {proc.returncode} : {(proc.stderr or '').strip()[:300]}")
                text = (proc.stdout or "").strip()
                if not text:
                    raise RuntimeError("sortie vide")
                return LLMResult(text, None, attempt, time.time() - started, "opencode", self.model)
            except subprocess.TimeoutExpired:
                last_error = f"timeout apres {self.timeout:.0f}s"
            except Exception as exc:  # noqa: BLE001 - on journalise tout
                last_error = str(exc)
            if attempt <= self.retries:
                time.sleep(self.backoff * attempt)
        return LLMResult("", last_error, self.retries + 1, time.time() - started, "opencode", self.model)


# --------------------------------------------------------------------------
# http_api : PLACEHOLDER
# --------------------------------------------------------------------------
class HttpApiClient:
    """PLACEHOLDER — non implemente volontairement.

    Si tu preferes un appel HTTP direct (cle API) plutot que le CLI opencode :
      1. ecrire ici l'appel POST (module 'urllib.request' de la stdlib suffit,
         pas besoin d'installer quoi que ce soit) ;
      2. convertir system+user en messages [{role, content}] ;
      3. retourner LLMResult(text, ...) comme les autres clients ;
      4. passer llm.backend a "http_api" dans settings.json.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def preflight(self) -> None:
        raise ClientError(
            "backend 'http_api' n'est pas encore code (placeholder dans src/llm_client.py).\n"
            "  -> utiliser llm.backend = 'opencode' ou 'dry_run'."
        )

    def generate(self, prompt: str, case: dict) -> LLMResult:
        raise ClientError("HttpApiClient non implemente (placeholder).")


def get_client(cfg: dict):
    backend = cfg["llm"]["backend"]
    if backend == "dry_run":
        return DryRunClient(cfg)
    if backend == "opencode":
        return OpencodeClient(cfg)
    if backend == "http_api":
        return HttpApiClient(cfg)
    raise ClientError(f"backend inconnu : {backend}")
