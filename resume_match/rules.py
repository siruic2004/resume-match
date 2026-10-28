"""Deterministic screening rules, run in the order the user cares about:

1. Citizenship / permanent work authorization / clearance  -> hard stop
2. Visa: OPT / CPT accepted, H-1B sponsorship (bonus)
3. Expected graduation date fits the posting's window
4. Skill keywords (fallback when no LLM key is configured)

The regexes work sentence by sentence so a negation in one sentence
("sponsorship is not available") doesn't get credited to a positive in another.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PASS, WARN, FAIL, UNCLEAR = "pass", "warn", "fail", "unclear"

# Patterns and thresholds live in rules.json, shared with the web page (scripts/build_web.py).
RULES = json.loads((Path(__file__).with_name("rules.json")).read_text(encoding="utf-8"))
US = RULES["US"]


def _expand(patterns: list[str]) -> list[str]:
    return [p.replace("{US}", US) for p in patterns]


@dataclass
class StepResult:
    step: str
    status: str
    summary: str
    evidence: list[str] = field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    # split on sentence ends, but not after abbreviations like "U.S." (uppercase before the dot)
    parts = re.split(RULES["sentence_split"], text)
    return [p.strip() for p in parts if len(p.strip()) > 3]


def _find(patterns: list[str], sentences: list[str], skip_negated=False) -> list[str]:
    hits = []
    for s in sentences:
        low = s.lower()
        if skip_negated and re.search(RULES["negation"], low):
            continue
        if any(re.search(p, low) for p in patterns):
            hits.append(s)
    return hits


# ------------------------------------------------------------ 1. citizenship

CITIZENSHIP_PATTERNS = _expand(RULES["citizenship"])


def check_citizenship(jd: str) -> StepResult:
    hits = _find(CITIZENSHIP_PATTERNS, split_sentences(jd), skip_negated=True)
    if hits:
        return StepResult("1. Citizenship / permanent authorization", FAIL,
                          "JD requires U.S. citizenship / permanent work authorization / clearance — skip.",
                          hits[:5])
    return StepResult("1. Citizenship / permanent authorization", PASS,
                      "No citizenship / green card / clearance requirement found.")


# ------------------------------------------------------------ 2. visa

NO_SPONSOR_PATTERNS = _expand(RULES["no_sponsorship"])
H1B_PATTERNS = _expand(RULES["h1b"])
OPT_CPT_PATTERNS = _expand(RULES["opt_cpt"])


def check_visa(jd: str, reject_no_sponsorship: bool = True) -> tuple[StepResult, str]:
    """Returns (result, label) with label in {h1b, opt_cpt, none_mentioned, no_sponsorship, mixed}."""
    step = "2. Visa (OPT / CPT / H-1B)"
    sents = split_sentences(jd)
    no = _find(NO_SPONSOR_PATTERNS, sents)
    positive_sents = [s for s in sents if s not in no]
    h1b = _find(H1B_PATTERNS, positive_sents)
    opt = _find(OPT_CPT_PATTERNS, positive_sents)

    if no and not (h1b or opt):
        status = FAIL if reject_no_sponsorship else WARN
        return StepResult(step, status, "JD says no visa sponsorship.", no[:4]), "no_sponsorship"
    if no:
        return StepResult(step, WARN,
                          "Mixed signals: some sponsorship limits, but OPT/CPT/H-1B mentioned. Read carefully.",
                          (no + h1b + opt)[:5]), "mixed"
    if h1b:
        return StepResult(step, PASS, "H-1B / visa sponsorship mentioned — best case.", (h1b + opt)[:4]), "h1b"
    if opt:
        return StepResult(step, PASS, "OPT / CPT / international students mentioned.", opt[:4]), "opt_cpt"
    return StepResult(step, UNCLEAR,
                      "No sponsorship language either way. Usually OK for CPT/OPT; H-1B unknown "
                      "(check the company on h1bdata.info / myvisajobs).", []), "none_mentioned"


# ------------------------------------------------------------ 3. graduation

GRAD = RULES["graduation"]
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
SEASONS = GRAD["season_month"]
DATE_RE = re.compile(GRAD["date"])  # groups: 1 month, 2 season, 3 year
GRAD_CONTEXT = re.compile(GRAD["context"])


def _parse_dates(sentence: str) -> list[tuple[int, int, bool]]:
    """(year, month, month_given) for each date mention."""
    out = []
    for m in DATE_RE.finditer(sentence):
        mon, season, year = m.group(1), m.group(2), int(m.group(3))
        if mon:
            out.append((year, MONTHS[mon[:3]], True))
        elif season:
            out.append((year, SEASONS[season], True))
        else:
            out.append((year, 0, False))
    return out


def check_graduation(jd: str, grad_year: int, grad_month: int) -> StepResult:
    step = "3. Graduation date"
    me = grad_year * 12 + grad_month
    good, bad, hints = [], [], []

    sents = split_sentences(jd)
    for i, s in enumerate(sents):
        low = s.lower()
        if not GRAD_CONTEXT.search(low) or re.search(GRAD["not_a_requirement"], low):
            continue
        dates = _parse_dates(low)
        nxt = sents[i + 1] if i + 1 < len(sents) else ""
        if not dates and nxt and len(nxt) <= GRAD["continuation_max_chars"] and _parse_dates(nxt.lower()):
            s = f"{s} {nxt}"
            low = s.lower()
            dates = _parse_dates(low)
        if not dates:
            if re.search(GRAD["still_enrolled"], low):
                hints.append(s)
            continue

        def key(d, upper=False):
            y, mo, given = d
            return y * 12 + (mo if given else (12 if upper else 1))

        if len(dates) >= 2 and re.search(GRAD["range"], low):
            lo, hi = key(dates[0]), key(dates[1], upper=True)
            (good if lo <= me <= hi else bad).append(s)
        elif re.search(GRAD["by"], low):
            (good if me <= key(dates[0], upper=True) else bad).append(s)
        elif re.search(GRAD["after"], low):
            (good if me >= key(dates[0]) else bad).append(s)
        elif re.search(GRAD["class_of"], low) or not dates[0][2]:
            (good if dates[0][0] == grad_year else bad).append(s)
        else:
            y, mo, _ = dates[0]
            (good if abs((y * 12 + mo) - me) <= GRAD["single_date_tolerance_months"] else bad).append(s)

    if bad and not good:
        return StepResult(step, FAIL, "Graduation window in JD doesn't include your date.", bad[:4])
    if bad and good:
        return StepResult(step, WARN, "Several graduation dates mentioned; some don't match.", (bad + good)[:4])
    if good:
        return StepResult(step, PASS, "Your graduation date is inside the JD's window.", good[:4])
    if hints:
        return StepResult(step, PASS, "JD wants students still enrolled after the role — fits you.", hints[:3])
    return StepResult(step, UNCLEAR, "No explicit graduation window found.", [])


# ------------------------------------------------------------ difficulty (fallback)

def estimate_difficulty(jd: str, title: str = "") -> tuple[int, str]:
    """0 = easy to land an interview, 100 = very hard. Rough keyword heuristic used
    when Claude isn't available; the point values are in rules.json["difficulty"]."""
    cfg = RULES["difficulty"]
    low = jd.lower()
    head = (title + " " + jd[:cfg["title_window_chars"]]).lower()
    score, why = cfg["base"], []

    years = [int(n) for s in split_sentences(low) if "experience" in s
             for n in re.findall(cfg["years_pattern"], s)]
    years = [y for y in years if y <= cfg["max_years_counted"]]
    if years:
        score += cfg["per_year_experience"] * max(years)
        why.append(f"要求 {max(years)}+ 年经验")
    if re.search(cfg["phd_required"]["pattern"], low):
        score += cfg["phd_required"]["points"]
        why.append("要求 PhD")
    elif re.search(cfg["phd_mentioned"]["pattern"], low):
        score += cfg["phd_mentioned"]["points"]
        why.append("PhD 优先")
    if re.search(cfg["senior_title"]["pattern"], head):
        score += cfg["senior_title"]["points"]
        why.append("高级职位")
    if re.search(cfg["entry_title"]["pattern"], head):
        score += cfg["entry_title"]["points"]
        why.append("实习 / 入门级")
    return max(0, min(100, score)), "；".join(why) or "无明显门槛信号"


# ------------------------------------------------------------ 4. skills (fallback)

SKILLS = RULES["skills"]
SDE_SKILLS: dict[str, str] = SKILLS["sde"]
DS_SKILLS: dict[str, str] = SKILLS["ds"]
LEARNABLE_DOMAINS: dict[str, str] = SKILLS["domains"]


def _hits(table: dict[str, str], text: str) -> list[str]:
    out = []
    for name, pat in table.items():
        f = 0 if name in SKILLS["case_sensitive"] else re.IGNORECASE
        if re.search(pat, text, f):
            out.append(name)
    return out


def check_skills_keywords(jd: str, resume: str) -> tuple[StepResult, int]:
    """Score the job's tool stack against the resume. Industry words (supply chain, finance, ...)
    are reported but never scored: an intern can pick those up; the technical bar is what counts.
    The title isn't used at all."""
    step = "4. Skills (keyword fallback)"
    jd_ds = _hits(DS_SKILLS, jd)
    have = set(_hits(DS_SKILLS, resume))
    jd_sde = _hits(SDE_SKILLS, jd)
    sde_have = set(_hits(SDE_SKILLS, resume))
    sde_missing = [s for s in jd_sde if s not in sde_have]
    domains = _hits(LEARNABLE_DOMAINS, jd)

    matched = [s for s in jd_ds if s in have]
    missing = [s for s in jd_ds if s not in have]
    score = int(100 * len(matched) / len(jd_ds) + 0.5) if jd_ds else SKILLS["no_tools_score"]
    score = max(0, score - SKILLS["sde_penalty_each"] * len(sde_missing))

    evidence = [
        f"Job's data tool stack: {', '.join(jd_ds) or '—'}",
        f"Matched: {', '.join(matched) or '—'}",
        f"Missing: {', '.join(missing) or '—'}",
        f"SDE skills in JD you lack: {', '.join(sde_missing) or '—'}",
        f"Industry knowledge (not scored, learnable on the job): {', '.join(domains) or '—'}",
    ]
    # An SDE job: several SWE skills you lack, and they outnumber the data tools in the JD.
    if len(sde_missing) >= SKILLS["sde_block_min"] and len(jd_sde) >= len(jd_ds):
        return StepResult(step, FAIL, f"Tech stack is mostly software engineering ({len(sde_missing)} SWE skills "
                                      f"you don't have vs. {len(jd_ds)} data tools).", evidence), score
    status = PASS if score >= SKILLS["pass_score"] else WARN if score >= SKILLS["warn_score"] else FAIL
    return StepResult(step, status, f"Tech-stack match {score}/100.", evidence), score
