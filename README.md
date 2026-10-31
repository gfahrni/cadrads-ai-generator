# CAD-RADS report generator

Generates **parameterizable** synthetic coronary CT angiography (CCTA) reports for
research on large language models. No patient data, no external dependency
(Python 3.10+ standard library only).

> **Status**: plumbing, plan, classification engine and checks are tested
> (71 tests, run in CI). The real call goes through the `opencode` CLI (see
> "Connecting opencode"); it could not be validated in the development
> environment. Only `backend: http_api` remains a placeholder.

---

## Quick start

Five steps, in order. Each one states what you should see before moving on.

### 1. Requirements

Python 3.10+ and the `opencode` CLI connected to your account. No `pip install`
needed: the project only uses the standard library (`requirements.txt` documents
that absence).

```bash
python3 --version      # 3.10 or later
opencode auth list     # must list at least one provider
```

### 2. Move into the project

```bash
cd cadrads-generator        # the folder that contains run.py
```

The name of your local folder does not matter: this is the Git repository as cloned.

### 3. Connect

```bash
python3 login.py
```

Pick `1`. OpenCode opens its own sign-in screen; on return, the script
immediately makes a real test call.

**Expected:** the message `Test SUCCEEDED`, then `Connection validated`.
`run.py` refuses to start without this step — that is intentional, not a bug.

### 4. Check the plan, without spending a call

```bash
python3 run.py --plan-only
python3 run.py --show-prompt
```

**Expected:** the 10 cases listed with their computed label, and
`target/computed drift: 0`.

### 5. One real case, then the corpus

```bash
python3 run.py --limit 1          # a single call
cat out/*/raw/SYN-0001.json       # prompt, raw response, checks
python3 run.py                    # everything settings.json asks for
```

**Expected:** at the end, the counters `label present`, `modifiers shown`,
`findings shown`.

### All the options at a glance

With no option at all, `run.py` runs the full pipeline: one model call per case,
reports in `reports/`, manifest and summaries.

| Option | Effect | Model call | Writes |
|---|---|---|---|
| `--settings PATH` | use another configuration file | — | — |
| `--limit N` | process only the first N cases | depends on the mode | depends on the mode |
| `--export-prompts` | also write each prompt to `prompts/` | yes | `prompts/*.md` + `index.tsv` |
| `--dry-run` | fake client, outputs isolated in `*-dryrun` | no | fabricated reports |
| `--prompt-only` | write every prompt, generate nothing | **no** | `prompts/*.md` + `index.tsv` |
| `--plan-only` | print the plan and the computed labels | no | nothing |
| `--show-prompt` | print the first case prompt | no | nothing |
| `--no-resume` | ignore the existing manifest | — | — |
| `--no-probe` | skip the connection check at startup | — | — |
| `--quiet` | minimal output | — | — |

`--dry-run`, `--plan-only`, `--show-prompt` and `--prompt-only` are **mutually
exclusive**: argparse rejects combinations and says so explicitly.
`--export-prompts` composes with a normal run or with a `--dry-run`.

`python3 run.py --help` lists the same thing, with the defaults.

`--prompt-only` is the way to review the 2,000 prompts **before** spending 2,000
calls:

```bash
python3 run.py --prompt-only
ls out/*/prompts/ | head
cat out/*/prompts/index.tsv
```

### If it does not start

| Message | What to do |
|---|---|
| `[LOGIN] No validated connection for this project.` | run `python3 login.py` |
| `[LOGIN] The connection was validated with another configuration` | you changed `llm.command` → re-run `login.py` |
| `Command not found: 'opencode'` | `which opencode` in that same terminal, otherwise fix `llm.command` |
| `Error: Model unavailable` | the model id does not exist for this provider → `login.py` option 2 |
| `[CONFIG] invalid settings.json:` | the message lists the faulty keys and the allowed values |
| dirty model output (preamble, logs) | see the table in point 4 of "Connecting opencode" |

**The only configuration file is `settings.json`.** `prompts/` and `reference/`
are editable too, but nothing is mandatory to get started.

## Project structure

```
cadrads-generator/            (repository root)
├── settings.json          ← config: the only file you edit
├── login.py               ← opencode connection + real test (before run.py)
├── run.py                 ← generation entry point
├── prompts/               system.md + user.md, editable (versioned in every run)
├── reference/
│   ├── cadrads_2_0.json           CAD-RADS rules (documentation)
│   └── incidental_findings.json   catalogue of incidental findings
├── src/
│   ├── config.py          loading + validation of settings.json
│   ├── planner.py         stratified draw and clinical scenarios
│   ├── classify.py        deterministic computation of the CAD-RADS label
│   ├── incidental.py      draw of the incidental findings
│   ├── prompt.py          prompt rendering
│   ├── llm_client.py      model call (opencode CLI; http_api = placeholder)
│   ├── auth.py            opencode connection + call probe
│   ├── validate.py        cleaning and automatic checks
│   └── store.py           manifest, resume, export
└── tests/                 71 tests, stdlib only
```

The files in `src/` are not meant to be edited by hand: all tuning happens through
`settings.json`, `prompts/` and `reference/`.

---

## Settings: settings.json

Everything is configured here. An unknown key is ignored; an invalid value stops
the program with a message listing the allowed values.

| Group | Key | Default | Effect |
|---|---|---|---|
| `run` | `n_reports` | `10` | number of reports to generate |
| `run` | `run_id` | `"auto"` | `"auto"` = new folder on every launch; a fixed value enables resume |
| `run` | `seed` | `42` | draw seed: same seed ⇒ same plan |
| `run` | `resume` | `true` | skip cases that already succeeded |
| `run` | `max_consecutive_errors` | `5` | stop everything if the model stops answering |
| `llm` | `backend` | `"opencode"` | `opencode`, `dry_run` or `http_api` |
| `llm` | `command` | `["opencode","run","--model",…,"{prompt}"]` | exact command; `{prompt}` is substituted |
| `llm` | `model_label` | `"opencode/grok-code"` | what is written into provenance — put version + date |
| `llm` | `timeout_s` / `retries` / `delay_between_calls_s` | `240` / `2` / `1.5` | robustness and throughput |
| `generation` | `languages` | `["en"]` | `en`, `fr`, `de`, `it` |
| `generation` | `style` | `"random"` | `free`, `structured`, `random` |
| `generation` | `cadrads_weights` | see file | target distribution of the categories |
| `generation` | `modifier_weights` | see file | frequency of S, G, E, V, N |
| `generation` | `age_range` / `sex` / `indication` | `[45,88]` / `random` / `stable` | simulated population |
| `other_structures` | `describe_normal` | `false` | describe the organs of the field of view |
| `other_structures` | `include_incidental` | `false` | add minor incidental findings |
| `other_structures` | `prevalence_scale` / `max_per_case` | `1.0` / `3` | dosing and cap of the findings |
| `checks` | `label_integrity` / `modifier_mention` / `incidental_mention` | `fail` / `warn` / `warn` | `fail` excludes, `warn` flags, `off` ignores |
| `checks` | `min_words` / `max_words` | `35` / `800` | accepted length |
| `export` | `filename` | `"{case_id}_{cadrads}_{lang}"` | name of the `.txt` files |

Every key also carries its explanation inside the file (`_doc`), so
`settings.json` stays readable without coming back here.

---

## The CAD-RADS label is computed, never generated

`src/classify.py` implements the CAD-RADS 2.0 rules and **computes** the label from
the lesions. That label is then **imposed** on the LLM in the prompt ("Reproduce
this classification verbatim"). Consequence: the label and the lesions in the
prompt cannot be inconsistent.

Rules applied, in order of precedence (on the most severe stenosis):

| # | Condition | Category |
|---|---|---|
| 1 | non-diagnostic segment **and** no stenosis ≥ 50 % | `N` |
| 2 | total occlusion (100 %) | `5` |
| 3 | left main ≥ 50 % **or** 3 vessels ≥ 70 % | `4B` |
| 4 | ≥ 70 % (1 or 2 vessels) | `4A` |
| 5 | 50–69 % | `3` |
| 6 | 25–49 % | `2` |
| 7 | 1–24 % | `1` |
| 8 | no plaque | `0` |

The final label is composed as `CAD-RADS <category>/<P>/<modifiers>`, with
modifiers sorted `N, S, G, E, V` (e.g. `CAD-RADS 3/P2/N/S`). Inapplicable
modifiers are dropped (no stent in CAD-RADS 0, no `N` without a non-diagnostic
segment) and the decision is traced in `classification_trace`.

**Guaranteed distribution.** The target category is used to generate a plausible
scenario, then the category is *recomputed* from the lesions. If the two diverge,
the computed value is kept and the case is flagged `target_ok: false`. Over 2,000
cases with the default weights: **0 drift**, exact distribution.

---

## Other structures and incidental findings

Two **independent options, disabled by default**, in `settings.json >
other_structures`:

| Option | Effect | Changes |
|---|---|---|
| `describe_normal` | the report describes lungs, pleura, mediastinum, aorta, pericardium as **normal** | the shape of the report |
| `include_incidental` | adds **minor** abnormalities drawn at their approximate prevalence | the content |

These are genuinely two different things: describing healthy organs (a complete
report) is not the same as adding incidental findings.

### The catalogue

`reference/incidental_findings.json` holds 14 findings (aorta, pericardium, valves,
ventricle, lungs, pleura, spine, upper abdomen), each with:

```json
{ "id": "aortic_calcification", "organ": "aorta",
  "label_en": "atherosclerotic calcification of the thoracic aorta",
  "prevalence": 0.30, "age_min": 60, "sex": "any" }
```

The draw applies `prevalence × prevalence_scale`, skips the finding when
`age < age_min` or when the sex does not match, and caps at `max_per_case`
(3 by default). Measured over 3,000 cases: observed prevalence **matches the
catalogue** (± 0.5 point), 34 % of patients with no finding at all, 1.07 on average
per patient.

> **Framing**: these are orders of magnitude accepted as **design parameters**, not
> a measured epidemiology. They ship with the code, are versioned in every run and
> can be edited; `prevalence_scale` allows a sensitivity analysis without recoding
> anything.

> **Effect of the cap**: with `max_per_case: 2`, frequent findings drop by about
> 12 % (the cap truncates the draw). At 3, the gap disappears. If you want exact
> prevalences, raise the cap.

### In the report

The findings are listed separately in the prompt and the model has to describe
them, placing them after the coronaries and before the impression. A check
verifies that they are **actually mentioned** (see the table below).

---

## Connecting opencode

### In a word

```bash
python3 login.py     # once, then whenever you change account or model
python3 run.py
```

`login.py` shows the connection status, lets you sign in (account) or paste an API
key, then makes **a real test call**. `run.py` **refuses to start** until that test
has succeeded, and repeats a short test at startup (1 call) to catch an account that
expired between two launches. `--no-probe` skips that test.

The green light is also **tied to the configuration**: if you change `llm.command`
(model, provider), the previous state is considered stale and `run.py` asks for a
`login.py` again. A "validated" obtained with one model is worth nothing for another.

`.state/login.json` contains only the *result* of the test (date, model, latency,
ok/failure). **Never a key.** The file is disposable: deleting it simply means going
through `login.py` again.

### The principle: two layers

Two well-separated layers — this is what protects your keys:

1. **OpenCode handles authentication.** You sign in once, the credentials go to
   `~/.local/share/opencode/auth.json`. The project never sees the key.
2. **The project only launches a command.** `settings.json > llm.command` is the
   exact template of that command.

> Never put a key in `settings.json`: that file is copied into
> `out/<run_id>/settings.snapshot.json` on every execution. The key would end up
> archived in the dataset outputs.

### 1. Connect the account

```bash
opencode auth login       # pick the provider, paste the key if asked
opencode auth list        # must list the connected provider
opencode models           # exact ids, provider/model format
opencode models --refresh # if a model was just added
```

### 2. Get the exact model identifier

`opencode models` returns `provider/model` lines. That string, and nothing else, is
what goes into `--model`.

> **Common trap**: "OpenCode Console" is a **provider** (the account), not a model.
> The free plan does not give a single model id: it gives access to the ones listed
> by `opencode models`. A stale id produces `Error: Model unavailable` — option 2 of
> `login.py` lists the real ids and can write the right one into `settings.json`.

### 3. Write the command

```json
"llm": {
  "backend": "opencode",
  "command": ["opencode", "run", "--model", "PROVIDER/MODEL", "{prompt}"],
  "model_label": "PROVIDER/MODEL@2026-10-31"
}
```

Recommended variant: isolate the agent in an empty folder, so it does not start
exploring the repository while writing a report.

```json
"command": ["opencode", "run", "--model", "PROVIDER/MODEL",
            "--dir", "/tmp/cadrads-scratch", "{prompt}"]
```

```bash
mkdir -p /tmp/cadrads-scratch   # once
```

`{prompt}` is replaced by the full prompt, as **a single argument** (no shell, no
escaping to handle). The prompt is ~2 KB: far below the `ARG_MAX` limit. The stdin
fallback of `src/llm_client.py` does not apply here, because `opencode run` expects
the message as a positional argument.

### 4. Test in the shell before running the pipeline

```bash
opencode run --model PROVIDER/MODEL --dir /tmp/cadrads-scratch \
  "Write a 3-line synthetic coronary CTA report ending with the line CAD-RADS 0"

opencode stats --days 1     # tokens and cost of the trial, to extrapolate 2000 calls
```

Check that the output contains **only** the report. If the agent adds a preamble,
logs or a code block, three options:

| Symptom | Fix |
|---|---|
| preamble / "Here is the report" | already handled by `clean()` in `src/validate.py` |
| code blocks (triple backtick) | already handled by `clean()` |
| mixed logs or events | add `--format json` then parse the events (to code in `llm_client.py`) |
| slow startup | `opencode serve` once, then add `--attach http://localhost:4096` |

### 5. Then in the pipeline

```bash
python3 run.py --show-prompt     # paste this prompt into the command from point 4
python3 run.py --limit 1         # 1 real call
cat out/*/raw/SYN-0001.json      # check prompt + raw response
python3 run.py --limit 10        # the control sample
```

### Choosing an API key instead of the account

Two routes, without changing anything in the project:

- `opencode auth login --provider <id>`: the key goes to `auth.json`, used
  automatically afterwards;
- provider environment variables (e.g. `ANTHROPIC_API_KEY`), which OpenCode reads at
  startup, including from a `.env` file in the current folder.

`backend: "http_api"` remains an empty placeholder in `src/llm_client.py`
(`HttpApiClient` class) if you want a direct HTTP call later.

## What is produced

```
out/<run_id>/
├── reports/              .txt accepted (the dataset)
├── prompts/              rendered prompts (with --export-prompts or --prompt-only)
│                         ├── SYN-0001.md  (the exact prompt, nothing added)
│                         └── index.tsv    case_id, label, language, style, sha
├── reports_rejected/     .txt excluded by an automatic check
├── raw/<case_id>.json    full prompt + raw response + checks + trace
├── manifest.jsonl        one JSON line per attempt (register + audit)
├── reports_index.csv     table ready to open
├── summary.json          counts per stratum, checks, plan drift
├── run.json              identifier + config fingerprint (guard)
├── settings.snapshot.json  exact configuration used
├── prompts.snapshot.json   exact prompts used
└── PROVENANCE.txt        timestamp, config fingerprint, model
```

With `--dry-run`, the `run_id` is suffixed `-dryrun` and the manifest carries
`dry_run: true`: fabricated outputs cannot contaminate a real run.

### What a report looks like

Each `.txt` is a complete report, ending with the imposed label:

```
Indication: evaluation of coronary artery disease in a 67-year-old patient.
Technique: ECG-gated coronary CT angiography, 0.5 mm slices.
Findings: non-calcified plaque of the proximal LAD with 82% luminal stenosis,
and a patent stent in the mid RCA.
Impression: severe stenosis of the proximal LAD.
CAD-RADS 4A/P2/S
```

*Shape example: the exact wording depends on the model and on the requested style
(free or structured).*

### What a manifest line looks like

```json
{"case_id": "SYN-0001", "status": "ok", "file": "reports/SYN-0001_4A_en.txt",
 "expected": {"cadrads": "4A", "label": "CAD-RADS 4A/P2/S", "language": "en"},
 "checks": {"ok": true, "results": {"label": {"ok": true, "category_found": "4A"}}},
 "meta": {"attempts": 1, "latency_s": 6.4, "model": "provider/model@2026-10-31",
          "prompt_sha": "1c8cb9d454eb41fb", "dry_run": false},
 "timestamp": "2026-10-31T12:05:00"}
```

`status` is `ok`, `excluded` (rejected by an automatic check) or `error` (technical
failure). This file is the source of truth for resume and audit: it is what lets you
republish a corpus without regenerating it.

---

## Resume

The manifest is the register: `resume: true` skips cases that already succeeded and
**retries** those that failed. `run.max_consecutive_errors` stops the run if the
model stops answering, rather than burning 2,000 calls.

**Resuming requires an explicit `run_id`.**

| `run.run_id` | Behaviour |
|---|---|
| `"auto"` (default) | **new folder on every launch** (timestamp + random suffix) → resume does not apply |
| `"study-1"` | fixed folder → resume works |

The random suffix prevents two launches within the same second from overwriting the
same folder. For a 2,000-report collection, **set `run_id`** from the start.

**Provenance guard**: a run folder cannot be reused with another configuration. The
`settings_fingerprint` is compared against `run.json`; on mismatch the program
refuses (exit 2) instead of producing a misleading snapshot.

---

## Automatic checks

| Check | Measure | Setting |
|---|---|---|
| `label` | the computed label is present, verbatim | `checks.label_integrity` |
| `modifiers` | are the stent / graft / non-diagnostic segment **described in the body** (not just in the label) | `checks.modifier_mention` |
| `incidental` | is each incidental finding mentioned in the body (match by organ) | `checks.incidental_mention` |
| `language` | detected language == requested language | `checks.language_match` |
| `length` | word count within the range | `checks.min_words` / `max_words` |
| `forbidden` | no "as an AI", no code block, no "language model" | `checks.forbidden_phrases` |

`fail` ⇒ the report is moved to `reports_rejected/`. `warn` ⇒ kept and flagged.
The rates are aggregated in `summary.json`.

These checks are **lexical**. A report can pass all of them and remain clinically
incoherent: that is precisely the role of the expert readers.

---

## Tests

```bash
python3 tests/test_classify.py     # 18 tests  — CAD-RADS rules
python3 tests/test_incidental.py   # 16 tests  — prevalences, gating, cap
python3 tests/test_validate.py     # 16 tests  — cleaning and checks
python3 tests/test_auth.py         #  8 tests  — connection state, failure path
python3 tests/test_cli.py          #  7 tests  — flags, exclusivity, export, docs up to date
python3 tests/test_config.py       #  6 tests  — rewriting the model in settings.json
```

They cover: stenosis thresholds, occlusion, left main, three vessels, `5 > 4B`
precedence, non-diagnostic cases, modifier filtering, plaque burden consistency,
label format; prevalences matching the catalogue, `age_min` / `sex` gating, cap,
exclusion, determinism; preamble and code-block cleaning, label tolerance, mentions
of modifiers and findings; connection state and its failure path.

71 tests, standard library only. They also run in continuous integration
(`.github/workflows/tests.yml`, Python 3.10 to 3.14): that is the proof that the
imposed label follows the rules and that the draw is reproducible.

---

## Reproducibility

- Deterministic draw: same `run.seed` ⇒ same plan (categories, ages, vessels, labels).
- `settings.snapshot.json` + `prompts.snapshot.json` + `PROVENANCE.txt` archived on every run.
- `raw/` keeps the raw response and the classification trace.
- **Pin** the model name with its version and date in `model_label`
  (e.g. `grok-code@2026-10-31`): a silently updated model makes the dataset
  non-reproducible.

---

## Before a big collection

1. All the test suites pass.
2. `llm.model_label` carries the **version and date** of the model.
3. Three reports reviewed with `--limit 3` before launching 2,000 calls.
4. `run_id` fixed in `settings.json`, otherwise no resume is possible.

## Tool limits

- **`5 > 4B` precedence**: when an occlusion and a left main lesion coexist, the
  higher category is kept. That is a convention; change it on one line in
  `classify_category` if you prefer the opposite.
- **Semi-quantitative plaque burden**: the consensus provides no closed formula;
  here `P` is constrained by the category and by the number of diseased vessels, not
  computed by an equation.
- **Simplified anatomy**: no dominance, no collaterals, no segmental calcium score,
  no CT-FFR.
- **Minor incidental findings only**: no mass, lymphadenopathy, pulmonary embolism or
  significant aneurysm. The corpus is therefore cleaner than a real cohort.
- **Lexical automatic checks**: a report can pass all of them and remain clinically
  incoherent.
- **Corpus homogeneity not measured** by the tool (post-processing: distinct-n,
  templates, embedding clusters).
- **Research use only.** These reports are not medical reports.

---

## License

MIT — see [LICENSE](LICENSE). The text includes a medical disclaimer: this software
produces synthetic reports for research, not medical reports.

If your institution requires Apache-2.0 or another license, simply replace the
`LICENSE` file and the `license` field of `CITATION.cff`.

## Citing this work

`CITATION.cff` provides the metadata (GitHub then shows a "Cite this repository"
button). Two fields must be corrected before publication:

- `repository-code`: the real repository URL;
- `date-released`.

Once the article is published, add the reference to the `preferred-citation` section.

## Data and availability

- The **code** is published here. It is enough to regenerate the corpus: the plan is
  deterministic (`run.seed`) and the prompts are versioned in every run.
- The generated **dataset** (`.txt` reports + `manifest.jsonl` + `summary.json`) does
  not belong in this repository: it is deposited on Zenodo with a DOI, and cited via
  the `preferred-citation` block of `CITATION.cff`.
- The **exact prompts** used are archived in every `out/<run_id>/`, so a published
  corpus remains auditable even if `prompts/` changes later.
