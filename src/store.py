"""Persistance : dossier de run, manifeste JSONL, reprise, export, resume.

Le manifeste est le registre de verite : une ligne JSON par tentative. Il sert
a la fois de journal d'audit et de mecanisme de reprise (on ne regenere jamais
un cas deja reussi).
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(name: str) -> str:
    return _UNSAFE.sub("-", name).strip("-") or "case"


class RunStore:
    def __init__(self, base_dir: Path, cfg: dict, run_id: str, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.run_id = f"{run_id}-dryrun" if dry_run else run_id
        self.dir = Path(base_dir) / cfg["run"]["output_dir"] / self.run_id
        self.reports_dir = self.dir / "reports"
        self.rejected_dir = self.dir / "reports_rejected"
        self.raw_dir = self.dir / "raw"
        self.manifest_path = self.dir / "manifest.jsonl"
        self.summary_path = self.dir / "summary.json"
        self.csv_path = self.dir / "reports_index.csv"
        self.prompts_dir = self.dir / "prompts"
        self.prompt_index_path = self.prompts_dir / "index.tsv"
        self.log_path = self.dir / "run.log"

    # -- cycle de vie -------------------------------------------------------
    def init(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.rejected_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def existing_fingerprint(self) -> str | None:
        """Empreinte de config du run deja present dans ce dossier, s'il existe."""
        meta_path = self.dir / "run.json"
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8")).get("settings_fingerprint")
        except json.JSONDecodeError:
            return None

    def snapshot_settings(self, cfg: dict, templates: dict, fingerprint: str) -> None:
        (self.dir / "run.json").write_text(
            json.dumps({
                "run_id": self.run_id,
                "settings_fingerprint": fingerprint,
                "dry_run": self.dry_run,
                "created": datetime.now().isoformat(timespec="seconds"),
            }, indent=2),
            encoding="utf-8",
        )
        (self.dir / "settings.snapshot.json").write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (self.dir / "prompts.snapshot.json").write_text(
            json.dumps(templates, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (self.dir / "PROVENANCE.txt").write_text(
            f"run_id           : {self.run_id}\n"
            f"created          : {datetime.now().isoformat(timespec='seconds')}\n"
            f"settings sha256  : {fingerprint}\n"
            f"backend          : {cfg['llm']['backend']}\n"
            f"model_label      : {cfg['llm'].get('model_label', '')}\n"
            f"dry_run          : {self.dry_run}\n",
            encoding="utf-8",
        )

    # -- journal ------------------------------------------------------------
    def log(self, message: str) -> None:
        line = f"{datetime.now().isoformat(timespec='seconds')} {message}\n"
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def append(self, record: dict) -> None:
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()

    def records(self) -> list:
        if not self.manifest_path.exists():
            return []
        out = []
        for line in self.manifest_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # ligne tronquee par un arret brutal : ignoree
        return out

    def done_ids(self) -> set:
        """Cas deja generes avec succes (dernier enregistrement par case_id).

        Les cas en echec ne sont PAS consideres comme faits : ils sont retentes
        a la reprise.
        """
        latest = {}
        for rec in self.records():
            latest[rec["case_id"]] = rec
        return {cid for cid, rec in latest.items() if rec.get("status") == "ok"}

    # -- ecriture -----------------------------------------------------------
    def filename_for(self, case: dict) -> str:
        template = self.cfg["export"]["filename"]
        fields = {
            "case_id": case["case_id"],
            "cadrads": case["cadrads"],
            "lang": case["language"],
            "style": case["style"],
        }
        return _safe(template.format(**fields)) + ".txt"

    def write_report(self, case: dict, text: str) -> Path:
        path = self.reports_dir / self.filename_for(case)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return path

    def write_rejected(self, case: dict, text: str) -> Path:
        """Rapport rejete par un controle automatique : garde a part, jamais
        melange au dataset final."""
        path = self.rejected_dir / self.filename_for(case)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return path

    def write_prompt(self, case: dict, prompt_text: str) -> Path:
        """Ecrit le prompt rendu, tel qu'il sera envoye (aucune en-tete ajoutee)."""
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        path = self.prompts_dir / f"{_safe(case['case_id'])}.md"
        path.write_text(prompt_text.rstrip() + "\n", encoding="utf-8")
        return path

    def write_prompt_index(self, cases) -> int:
        """Index TSV reconstruit depuis les fichiers presents (idempotent, resumable)."""
        if not self.prompts_dir.exists():
            return 0
        by_id = {case["case_id"]: case for case in cases}
        rows = []
        for path in sorted(self.prompts_dir.glob("*.md")):
            case = by_id.get(path.stem, {})
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            rows.append([
                path.stem,
                case.get("label", ""),
                case.get("language", ""),
                case.get("style", ""),
                digest,
                path.name,
            ])
        with self.prompt_index_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, delimiter="\t")
            writer.writerow(["case_id", "label", "language", "style", "prompt_sha", "file"])
            writer.writerows(rows)
        return len(rows)

    def write_raw(self, case_id: str, payload: dict) -> None:
        path = self.raw_dir / f"{_safe(case_id)}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def write_summary(self, summary: dict) -> None:
        self.summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def write_csv(self, records: list) -> None:
        if not self.cfg["export"].get("write_csv_index", True):
            return
        latest = {}
        for rec in records:
            latest[rec["case_id"]] = rec
        with self.csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "case_id", "status", "cadrads", "label_expected", "label_ok",
                "language", "language_ok", "style", "age", "sex", "modifiers",
                "modifiers_ok", "incidental", "incidental_ok", "words",
                "attempts", "latency_s", "file",
            ])
            for cid in sorted(latest):
                rec = latest[cid]
                checks = rec.get("checks", {}).get("results", {})
                writer.writerow([
                    cid,
                    rec.get("status", ""),
                    rec.get("expected", {}).get("cadrads", ""),
                    checks.get("label", {}).get("expected", ""),
                    checks.get("label", {}).get("ok", ""),
                    rec.get("expected", {}).get("language", ""),
                    checks.get("language", {}).get("ok", ""),
                    rec.get("expected", {}).get("style", ""),
                    rec.get("expected", {}).get("age", ""),
                    rec.get("expected", {}).get("sex", ""),
                    "|".join(rec.get("expected", {}).get("modifiers", [])),
                    checks.get("modifiers", {}).get("ok", ""),
                    "|".join(rec.get("expected", {}).get("incidental", [])),
                    checks.get("incidental", {}).get("ok", ""),
                    checks.get("length", {}).get("words", ""),
                    rec.get("meta", {}).get("attempts", ""),
                    round(rec.get("meta", {}).get("latency_s", 0.0), 2),
                    rec.get("file", ""),
                ])
