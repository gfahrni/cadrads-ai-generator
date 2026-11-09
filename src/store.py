"""Persistence: run folder, JSONL manifest, resume, export, summary.

The manifest is the register of truth: one JSON line per attempt. It serves both as
an audit log and as the resume mechanism (a case that already succeeded is never
regenerated).
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from . import xlsx

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _grade_of(case: dict, slot: int) -> str:
    """CAD-RADS grade of the lesion at this position, from the plan when available."""
    per_lesion = case.get("per_lesion") or []
    if slot < len(per_lesion):
        return per_lesion[slot]["cadrads"]
    return ""


def _unplanned_organs(checks: dict) -> str:
    """Organs of the minor findings described but not planned (empty = clean)."""
    extras = checks.get("unplanned_incidental", {}).get("detected") or []
    return "|".join(sorted({organ for extra in extras for organ in extra.get("organs", [])}))


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
        self.prompts_dir = self.dir / "prompts"
        self.prompt_index_path = self.prompts_dir / "index.tsv"
        self.manifest_path = self.dir / "manifest.jsonl"
        self.summary_path = self.dir / "summary.json"
        self.csv_path = self.dir / "reports_index.csv"
        self.log_path = self.dir / "run.log"

    # -- lifecycle ----------------------------------------------------------
    def init(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.rejected_dir.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def existing_fingerprint(self) -> str | None:
        """Config fingerprint of the run already present in this folder, if any."""
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

    # -- log ----------------------------------------------------------------
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
                continue  # line truncated by a hard stop: ignored
        return out

    def done_ids(self) -> set:
        """Cases already generated successfully (last record per case_id).

        Failed cases are NOT considered done: they are retried on resume.
        """
        latest = {}
        for rec in self.records():
            latest[rec["case_id"]] = rec
        return {cid for cid, rec in latest.items() if rec.get("status") == "ok"}

    # -- writing ------------------------------------------------------------
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
        """Report rejected by an automatic check: kept aside, never mixed into the
        final dataset."""
        path = self.rejected_dir / self.filename_for(case)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return path

    def write_prompt(self, case: dict, prompt_text: str) -> Path:
        """Write the rendered prompt exactly as it will be sent (no header added)."""
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        path = self.prompts_dir / f"{_safe(case['case_id'])}.md"
        path.write_text(prompt_text.rstrip() + "\n", encoding="utf-8")
        return path

    def write_prompt_index(self, cases) -> int:
        """TSV index rebuilt from the files on disk (idempotent, resumable)."""
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


    # -- dataset descriptor ------------------------------------------------
    DESCRIPTOR_COLUMNS = [
        "case_id", "status", "file",
        "cadrads", "target_cadrads", "target_ok", "label",
        "plaque_burden", "modifiers", "n_lesions",
        "lesion_1_vessel", "lesion_1_segment", "lesion_1_stenosis", "lesion_1_plaque", "lesion_1_cadrads",
        "lesion_2_vessel", "lesion_2_segment", "lesion_2_stenosis", "lesion_2_plaque", "lesion_2_cadrads",
        "lesion_3_vessel", "lesion_3_segment", "lesion_3_stenosis", "lesion_3_plaque", "lesion_3_cadrads",
        "agatston", "age", "sex", "indication", "language", "style", "technique",
        "stent_site", "graft_site", "vulnerable_site", "exception_site", "non_evaluable",
        "incidental", "words", "label_ok", "modifiers_ok", "incidental_ok",
        "attempts", "latency_s", "model", "prompt_sha", "unplanned_incidental",
    ]

    def write_dataset_descriptor(self, cases, records):
        """One row per report: every input field of the scenario, plus the outcome.

        Written as a real .xlsx (no dependency), next to the CSV index. This is the
        file to open in a spreadsheet to check the corpus composition.
        """
        if not self.cfg["export"].get("write_xlsx", True):
            return None

        latest = {}
        for record in records:
            latest[record["case_id"]] = record

        rows = []
        for case in cases:
            record = latest.get(case["case_id"])
            if record is None:
                continue
            checks = record.get("checks", {}).get("results", {})
            lesions = case["lesions"]
            row = [
                case["case_id"], record.get("status", ""), record.get("file", ""),
                case["cadrads"], case["target_cadrads"], case["target_ok"], case["label"],
                case["plaque_burden"] or "", "|".join(case["modifiers"]), len(lesions),
            ]
            for slot in range(3):
                if slot < len(lesions):
                    lesion = lesions[slot]
                    row += [lesion["vessel"], lesion["segment"], lesion["stenosis_pct"],
                            lesion["plaque"], _grade_of(case, slot)]
                else:
                    row += ["", "", "", "", ""]
            row += [
                case["agatston"], case["age"], case["sex"], case["indication"],
                case["language"], case["style"], case["technique"],
                case["stent_site"] or "", case["graft_site"] or "",
                case["vulnerable_site"] or "", case["exception_site"] or "",
                "; ".join(case["non_evaluable"]),
                "|".join(f["id"] for f in case.get("incidental") or []),
                checks.get("length", {}).get("words", ""),
                checks.get("label", {}).get("ok", ""),
                checks.get("modifiers", {}).get("ok", ""),
                checks.get("incidental", {}).get("ok", ""),
                record.get("meta", {}).get("attempts", ""),
                record.get("meta", {}).get("latency_s", ""),
                record.get("meta", {}).get("model", ""),
                record.get("meta", {}).get("prompt_sha", ""),
                _unplanned_organs(checks),
            ]
            rows.append(row)

        return xlsx.write(self.dir / "dataset.xlsx", self.DESCRIPTOR_COLUMNS, rows,
                          sheet_name="dataset", widths={"technique": 42, "label": 22, "file": 34})

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
                "attempts", "latency_s", "file", "unplanned_incidental",
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
                    _unplanned_organs(checks),
                ])
