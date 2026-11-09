"""Response cleaning and automatic checks.

Two families of checks, not to be confused:

- `label`: does the report contain the CAD-RADS label computed by
  src/classify.py? This is an INTEGRITY check (does the model transcribe it
  correctly?), not a measure of clinical accuracy: the label is imposed.
- `modifiers`: are the announced modifiers (stent, graft, non-diagnostic segment)
  described in the body of the report? A light semantic check, which catches labels
  pasted without the matching content.
- `unplanned_incidental`: does the body describe a minor non-coronary abnormality
  that the plan did NOT contain? The `incidental` check above cannot see those: it
  is silent by construction when the plan has no finding at all.

Plus the shape checks: language, length, forbidden phrases.
"""
from __future__ import annotations

import re

# --- cleaning --------------------------------------------------------------

_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*")
_PREAMBLE = re.compile(
    r"^\s*(here (is|are)\b[^\n]*:|below is\b[^\n]*:|voici\b[^\n]*:|ci-dessous\b[^\n]*:|"
    r"sure[,!]?\s*$|certainly[,!]?\s*$)\s*",
    re.IGNORECASE,
)
_CAD_RADS_LINE = re.compile(r"CAD[-\s]?RADS\s*[™®]?\s*[:=]?\s*([0-5])\s*([A-B])?", re.IGNORECASE)


def clean(raw: str) -> str:
    """Remove the usual LLM noise (code fences, preamble, quotes)."""
    text = _FENCE.sub("", raw or "")
    text = text.strip()

    for _ in range(3):
        new = _PREAMBLE.sub("", text, count=1)
        if new == text:
            break
        text = new.lstrip()

    if len(text) > 2 and text[0] == text[-1] and text[0] in "\"'«»":
        text = text[1:-1]

    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --- language detection (light heuristic) ----------------------------------

_STOPWORDS = {
    "en": ("the", "and", "with", "of", "is", "there", "no", "coronary"),
    "fr": ("le", "la", "les", "des", "et", "avec", "est", "pas", "coronaire"),
    "de": ("der", "die", "und", "mit", "ist", "nicht", "keine", "koronar"),
    "it": ("il", "la", "gli", "e", "con", "non", "coronaria", "nessuna"),
}


def detect_language(text: str) -> str | None:
    lowered = " " + text.lower() + " "
    scores = {
        lang: sum(len(re.findall(rf"\b{re.escape(w)}\b", lowered)) for w in words)
        for lang, words in _STOPWORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else None


# --- imposed label ---------------------------------------------------------

def extract_cadrads(text: str) -> str | None:
    """Last CAD-RADS mention of the text -> category ('4A', '0'...). For debugging."""
    matches = list(_CAD_RADS_LINE.finditer(text))
    if not matches:
        return None
    digits, letter = matches[-1].group(1), matches[-1].group(2)
    return f"{digits}{(letter or '').upper()}"


def _label_pattern(label: str) -> re.Pattern:
    """Tolerant regex: 'CAD-RADS 4A/P2/S' accepts spaces and hyphen variants."""
    parts = []
    for token in re.split(r"(/|\s+)", label):
        if not token:
            continue
        if token == "/":
            parts.append(r"\s*/\s*")
        elif token.strip() == "":
            parts.append(r"\s+")
        else:
            parts.append(re.escape(token).replace(r"\-", "[- ]?"))
    return re.compile("".join(parts), re.IGNORECASE)


def label_present(text: str, label: str) -> bool:
    return _label_pattern(label).search(text) is not None


def _body_only(text: str) -> str:
    """Text without the final classification line."""
    matches = list(_CAD_RADS_LINE.finditer(text))
    return text[: matches[-1].start()] if matches else text


# --- modifiers actually described ------------------------------------------
# The keyword tables are multilingual on purpose: they must match the report
# language (en/fr/de/it), so the non-English entries are data, not prose.
_MODIFIER_KEYWORDS = {
    "S": ("stent",),
    "G": ("graft", "bypass", "pontage", "bypassgraft"),
    "E": ("anomal", "abnormal", "exception", "fistul", "aberrant", "aneurysm",
          "origine anormale", "anévrisme"),
    "N": ("non-diagnostic", "non diagnostic", "non-diagnostique", "non diagnostique",
          "non-evaluable", "non evaluable", "non eval", "artefact", "artifact",
          "nicht beurteilbar", "non valutabile"),
    "V": ("vulnerab", "vulnéra", "high-risk plaque", "high risk plaque", "remodel"),
}


def _mentions(text: str, modifiers) -> dict:
    body = _body_only(text).lower()
    return {m: any(k in body for k in _MODIFIER_KEYWORDS.get(m, (m.lower(),))) for m in modifiers}


def _incidental_mention(body: str, findings) -> dict:
    """Is each incidental finding mentioned in the body?

    None = not verifiable: the catalogue provides no keyword for the report
    language (other than en/fr/de/it).
    """
    mentioned = {}
    for finding in findings:
        keywords = finding.get("keywords") or []
        mentioned[finding["id"]] = any(k in body for k in keywords) if keywords else None
    return mentioned


# --- minor findings that were NOT planned -----------------------------------
# `describe_normal` asks the model to write a paragraph on the lungs, pleura,
# mediastinum, aorta and pericardium. Nothing keeps that paragraph generic: the
# model can slip in a minor abnormality the plan never contained ("minimal
# dependent atelectasis"). That is a hallucination, and the `incidental` check
# above cannot see it: it only looks at the findings that WERE planned, and stays
# silent when the plan has none.
#
# The detection is deliberately shallow, and conservative by design (a false
# positive here would be noise in every report):
#
#   - the body is cut into clauses;
#   - a clause counts only if it names an organ of the catalogue, in the report
#     language (organ keywords are data: reference/incidental_findings.json);
#   - a clause naming an organ that DOES carry a planned finding is left alone: it
#     is taken to describe that finding;
#   - a negated clause is left alone ("no pleural effusion", "pericardium
#     unremarkable", "sans épanchement");
#   - an abnormality stem must be present ("atelecta", "calcif", "épanchement"...).
_ABNORMALITY_STEMS = (
    # en
    "calcif", "dilat", "ectas", "effusion", "atelecta", "emphysem", "nodul",
    "hernia", "steatos", "gallstone", "cholelith", "thicken", "degenerat",
    "hypertroph", "scleros", "consolidat", "fractur", "lymphadenopath", "opacit",
    "granulom", "atheroscler", "aneurysm", "cyst",
    # fr
    "épanchement", "atélectasie", "emphysème", "nodule", "hernie", "stéatose",
    "calcul", "épaississement", "dégénérat", "hypertrophie", "sclérose",
    "consolidation", "fracture", "adénopath", "opacité", "granulome",
    "athérosclér", "anévrism", "kyste",
    # de
    "verkalk", "erweiter", "ektas", "erguss", "atelektase", "emphysem", "knoten",
    "hernie", "steatose", "gallengang", "verdick", "degenerativ", "hypertroph",
    "skleros", "raumforderung", "fraktur", "zyste", "lymphadenopath",
    "verschattung", "aneurysma",
    # it
    "calcific", "dilatazione", "versamento", "atelectasia", "enfisema", "nodulo",
    "ernia", "steatosi", "calcol", "ispessimento", "degenerativ", "ipertrofi",
    "scleros", "consolidamento", "frattura", "linfadenopat", "opacit", "granulom",
    "ateroscleros", "aneurisma", "cisti",
)

# A negation anywhere in the clause cancels the finding: reports describe normal
# organs with "no / unremarkable / normal", and the four languages are mixed here
# for the same reason as the modifier keywords above.
_NEGATION = re.compile(
    r"\b(no|not|without|absence|absent|unremarkable|normal|intact|clear|none|free)\b"
    r"|\b(pas|sans|aucun|aucune|non|absenc|normale?s?|normaux|intact|libre|respecté)\b"
    r"|\b(kein|keine|ohne|nicht|unauffällig|regelrecht|intakt)\b"
    r"|\b(nessun|nessuna|senza|assenza|normale|indenne|libero)\b"
)

_CLAUSE_SPLIT = re.compile(r"[.;:\n•·–—]+")


def _unplanned_findings(text: str, case: dict, catalogue: dict | None) -> list:
    """Minor findings described in the body although the plan contains none.

    Returns [{"organs": [...], "excerpt": "..."}], one entry per suspicious clause.
    Empty when the catalogue is not supplied (nothing to match the organs against):
    the caller then reports the check as skipped.
    """
    if not catalogue:
        return []

    language = case.get("language") or "en"
    organs = catalogue.get("organs", {})
    planned = {f.get("organ") for f in case.get("incidental") or []}

    found = []
    for clause in _CLAUSE_SPLIT.split(_body_only(text)):
        lowered = clause.lower()
        mentioned = [organ for organ, table in organs.items()
                     if any(k in lowered for k in table.get(language, []))]
        if not mentioned:
            continue
        if any(organ in planned for organ in mentioned):
            continue  # taken to describe a planned finding
        if _NEGATION.search(lowered):
            continue  # "no pleural effusion", "péricarde normal"
        if not any(stem in lowered for stem in _ABNORMALITY_STEMS):
            continue
        found.append({"organs": mentioned, "excerpt": clause.strip()[:160]})
    return found


# --- main check ------------------------------------------------------------

def check(text: str, case: dict, cfg: dict, catalogue: dict | None = None) -> dict:
    checks_cfg = cfg["checks"]
    words = len(text.split())

    def status(key, ok):
        """Turn a boolean plus the checks.* policy into a symbolic status."""
        mode = checks_cfg.get(key, "warn")
        if mode == "off" or ok:
            return "ok" if ok else "off"
        return mode  # "fail" or "warn"

    expected_label = case["label"]
    label_ok = label_present(text, expected_label)

    detected_lang = detect_language(text)
    if detected_lang is None:
        # Language not determinable (very short text): neither success nor failure.
        language_ok = False
        language_status = "warn"
    else:
        language_ok = detected_lang == case["language"]
        language_status = status("language_match", language_ok)

    modifiers = case["modifiers"]
    mentioned = _mentions(text, modifiers) if modifiers else {}
    modifiers_ok = all(mentioned.values()) if mentioned else True

    findings = case.get("incidental") or []
    if not findings:
        incidental_mention = {}
        incidental_ok = True
        incidental_status = "off"
        incidental_skipped = True  # nothing to check: out of the denominator
    else:
        incidental_mention = _incidental_mention(_body_only(text).lower(), findings)
        verifiable = {k: v for k, v in incidental_mention.items() if v is not None}
        incidental_skipped = not verifiable
        incidental_ok = all(verifiable.values()) if verifiable else True
        incidental_status = "off" if incidental_skipped else status("incidental_mention", incidental_ok)

    lowered = text.lower()
    hits = [p for p in checks_cfg["forbidden_phrases"] if p.lower() in lowered]

    extras = _unplanned_findings(text, case, catalogue)
    extras_skipped = catalogue is None

    length_ok = checks_cfg["min_words"] <= words <= checks_cfg["max_words"]

    results = {
        "label": {
            "expected": expected_label,
            "ok": label_ok,
            "category_found": extract_cadrads(text),
            "status": status("label_integrity", label_ok),
        },
        "language": {
            "value": detected_lang,
            "expected": case["language"],
            "ok": language_ok,
            "status": language_status,
            "undetermined": detected_lang is None,
        },
        "modifiers": {
            "expected": modifiers,
            "mentioned": mentioned,
            "ok": modifiers_ok,
            "status": status("modifier_mention", modifiers_ok),
        },
        "incidental": {
            "expected": [f["id"] for f in findings],
            "mentioned": incidental_mention,
            "ok": incidental_ok,
            "status": incidental_status,
            "skipped": incidental_skipped,
        },
        "unplanned_incidental": {
            "detected": extras,
            "ok": not extras,
            "status": "off" if extras_skipped else status("unplanned_incidental", not extras),
            "skipped": extras_skipped,
        },
        "length": {"words": words, "ok": length_ok, "status": "ok" if length_ok else "warn"},
        "forbidden": {"hits": hits, "ok": not hits, "status": "ok" if not hits else "warn"},
    }

    statuses = [v["status"] for v in results.values()]
    return {"ok": "fail" not in statuses, "results": results}
