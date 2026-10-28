"""Deterministic screening rules, run in the order the user cares about:

1. Citizenship / permanent work authorization / clearance  -> hard stop
2. Visa: OPT / CPT accepted, H-1B sponsorship (bonus)
3. Expected graduation date fits the posting's window
4. Skill keywords (fallback when no LLM key is configured)

The regexes work sentence by sentence so a negation in one sentence
("sponsorship is not available") doesn't get credited to a positive in another.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PASS, WARN, FAIL, UNCLEAR = "pass", "warn", "fail", "unclear"

US = r"(?:u\.?\s?s\.?(?:a\.?)?|united\s+states)"


@dataclass
class StepResult:
    step: str
    status: str
    summary: str
    evidence: list[str] = field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    # split on sentence ends, but not after abbreviations like "U.S." (uppercase before the dot)
    parts = re.split(r"(?<=[a-z0-9)][.!?])\s+|(?<=;)\s+|\n+|•|·", text)
    return [p.strip() for p in parts if len(p.strip()) > 3]


def _find(patterns: list[str], sentences: list[str], skip_negated=False) -> list[str]:
    hits = []
    for s in sentences:
        low = s.lower()
        if skip_negated and re.search(r"\b(?:not|no|never)\s+(?:be\s+)?(?:required|necessary|needed)", low):
            continue
        if any(re.search(p, low) for p in patterns):
            hits.append(s)
    return hits


# ------------------------------------------------------------ 1. citizenship

CITIZENSHIP_PATTERNS = [
    rf"\b{US}\s+citizen(?:ship)?\s+(?:is\s+|are\s+)?(?:required|mandatory|needed|a\s+must)",
    rf"\bmust\s+(?:be|hold|have|possess)\s+(?:a\s+)?{US}\s+citizen",
    rf"\brequires?\s+{US}\s+citizenship",
    rf"\b(?:only\s+{US}\s+citizens|{US}\s+citizens?\s+only)",
    rf"\b{US}\s+citizens?\s+(?:or|and/or|/)\s+(?:lawful\s+)?(?:permanent\s+residents?|green\s*card)",
    r"\bcitizenship\s+(?:is\s+)?required",
    r"\bmust\s+be\s+(?:a\s+)?(?:citizen|permanent\s+resident|green\s*card\s+holder)",
    r"\bpermanent\s+(?:\w+\s+){0,2}work\s+authori[sz]ation",
    r"\bauthori[sz]ed\s+to\s+work\b.{0,60}\bpermanent(?:ly|\s+basis)?\b",
    r"\b(?:green\s*card|permanent\s+residents?)\s+(?:holders?\s+)?(?:only|required)",
    r"\b(?:active|current|existing|obtain|maintain|eligib\w*\s+(?:to|for))\s+(?:an?\s+)?(?:\w+\s+){0,2}security\s+clearance",
    r"\bsecurity\s+clearance\s+(?:is\s+)?required",
    r"\b(?:ts/sci|top\s+secret(?:\s+clearance)?|secret\s+clearance)\b",
    r"\bitar\b",
    rf"\b{US}\s+persons?\b.{0,40}\b(?:itar|ear|export|22\s*cfr|required)",
    r"\bexport\s+control\w*\b.{0,80}\b(?:citizen|u\.?s\.?\s+person)",
]


def check_citizenship(jd: str) -> StepResult:
    hits = _find(CITIZENSHIP_PATTERNS, split_sentences(jd), skip_negated=True)
    if hits:
        return StepResult("1. Citizenship / permanent authorization", FAIL,
                          "JD requires U.S. citizenship / permanent work authorization / clearance — skip.",
                          hits[:5])
    return StepResult("1. Citizenship / permanent authorization", PASS,
                      "No citizenship / green card / clearance requirement found.")


# ------------------------------------------------------------ 2. visa

NO_SPONSOR_PATTERNS = [
    r"\b(?:unable|not\s+able|cannot|can't|can\s+not|will\s+not|won't|does\s+not|do\s+not|doesn't|don't|not\s+in\s+a\s+position)\s+(?:to\s+)?(?:\w+\s+){0,3}sponsor",
    r"\bno\s+(?:\w+\s+)?(?:visa\s+|immigration\s+)?sponsorship",
    r"\bsponsorship\s+(?:is\s+|will\s+)?(?:not|n't)\b",
    r"\bsponsorship\s+(?:is\s+)?unavailable",
    r"\bwithout\s+(?:the\s+)?(?:need\s+for\s+|requiring\s+|requirement\s+of\s+)?(?:current\s+or\s+future\s+|future\s+)?(?:\w+\s+){0,2}sponsorship",
    r"\bnot\s+(?:be\s+)?eligible\s+for\s+(?:\w+\s+)?sponsorship",
    r"\b(?:opt|cpt|f-?1|stem\s+opt)\b.{0,50}\b(?:not\s+(?:eligible|accepted|considered|supported)|ineligible)",
]
H1B_PATTERNS = [
    r"\bh-?1-?b\b.{0,40}\b(?:sponsor|available|offered|support|transfer)",
    r"\b(?:visa|immigration)\s+sponsorship\s+(?:is\s+|may\s+be\s+)?(?:available|offered|provided|possible)",
    r"\b(?:will|can|does|do|may|able\s+to)\s+(?:also\s+)?sponsor",
    r"\bsponsorship\s+(?:is\s+|may\s+be\s+)?available",
    r"\bopen\s+to\s+sponsor",
]
OPT_CPT_PATTERNS = [
    r"\b(?:stem\s+)?opt\b",
    r"\bcpt\b",
    r"\bf-?1\b",
    r"\binternational\s+students?\b",
    r"\bcurricular\s+practical\s+training\b",
    r"\boptional\s+practical\s+training\b",
]


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

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
SEASONS = {"spring": 5, "summer": 8, "fall": 12, "autumn": 12, "winter": 12}
DATE_RE = re.compile(
    r"\b(?:(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+"
    r"|(?P<season>spring|summer|fall|autumn|winter)\s+)?(?:of\s+)?(?P<year>20[2-3]\d)\b"
)
GRAD_CONTEXT = re.compile(
    r"graduat|class\s+of|degree\s+(?:completion|conferral)|complet\w*\s+(?:your|their|a|the)?\s*degree|"
    r"expected\s+(?:to\s+)?(?:graduat|complet)|enrolled|pursuing|returning\s+to\s+school|remaining\s+after"
)


def _parse_dates(sentence: str) -> list[tuple[int, int, bool]]:
    """(year, month, month_given) for each date mention."""
    out = []
    for m in DATE_RE.finditer(sentence):
        year = int(m.group("year"))
        if m.group("mon"):
            out.append((year, MONTHS[m.group("mon")[:3]], True))
        elif m.group("season"):
            out.append((year, SEASONS[m.group("season")], True))
        else:
            out.append((year, 0, False))
    return out


def check_graduation(jd: str, grad_year: int, grad_month: int) -> StepResult:
    step = "3. Graduation date"
    me = grad_year * 12 + grad_month
    good, bad, hints = [], [], []

    for s in split_sentences(jd):
        low = s.lower()
        if not GRAD_CONTEXT.search(low):
            continue
        dates = _parse_dates(low)
        if not dates:
            if re.search(r"returning\s+to\s+school|remaining\s+after|at\s+least\s+one\s+(?:more\s+)?(?:semester|quarter|term)", low):
                hints.append(s)
            continue

        def key(d, upper=False):
            y, mo, given = d
            return y * 12 + (mo if given else (12 if upper else 1))

        if len(dates) >= 2 and re.search(r"between|from|\bto\b|through|-|–", low):
            lo, hi = key(dates[0]), key(dates[1], upper=True)
            (good if lo <= me <= hi else bad).append(s)
        elif re.search(r"\b(?:by|before|no\s+later\s+than|prior\s+to)\b", low):
            (good if me <= key(dates[0], upper=True) else bad).append(s)
        elif re.search(r"\b(?:after|or\s+later|no\s+earlier\s+than)\b", low):
            (good if me >= key(dates[0]) else bad).append(s)
        elif re.search(r"\bclass\s+of\b", low) or not dates[0][2]:
            (good if dates[0][0] == grad_year else bad).append(s)
        else:
            y, mo, _ = dates[0]
            (good if abs((y * 12 + mo) - me) <= 3 else bad).append(s)

    if bad and not good:
        return StepResult(step, FAIL, "Graduation window in JD doesn't include your date.", bad[:4])
    if bad and good:
        return StepResult(step, WARN, "Several graduation dates mentioned; some don't match.", (bad + good)[:4])
    if good:
        return StepResult(step, PASS, "Your graduation date is inside the JD's window.", good[:4])
    if hints:
        return StepResult(step, PASS, "JD wants students still enrolled after the role — fits you.", hints[:3])
    return StepResult(step, UNCLEAR, "No explicit graduation window found.", [])


# ------------------------------------------------------------ 4. skills (fallback)

SDE_SKILLS = {
    "C++": r"\bc\+\+", "C#": r"\bc#", "Java": r"\bjava\b(?!\s*script)", "Go/Golang": r"\bgolang\b",
    "Rust": r"\brust\b", "Kotlin": r"\bkotlin\b", "Swift/iOS": r"\bswift\b|\bios\s+develop",
    "Android": r"\bandroid\b", "React": r"\breact(?:\.js)?\b", "Angular": r"\bangular\b",
    "Vue": r"\bvue(?:\.js)?\b", "Node.js": r"\bnode(?:\.js)?\b", "TypeScript": r"\btypescript\b",
    "JavaScript": r"\bjavascript\b", "Frontend": r"\bfront[\s-]?end\b", "Backend": r"\bback[\s-]?end\b",
    "Full-stack": r"\bfull[\s-]?stack\b", "Microservices": r"\bmicroservices?\b",
    "Distributed systems": r"\bdistributed\s+systems?\b", "System design": r"\bsystem\s+design\b",
    "Kubernetes": r"\bkubernetes\b|\bk8s\b", "Embedded/firmware": r"\bembedded\b|\bfirmware\b|\brtos\b",
    "Verilog/FPGA": r"\bverilog\b|\bvhdl\b|\bfpga\b", "Low-latency": r"\blow[\s-]latency\b",
    "Compilers/OS": r"\bcompilers?\b|\boperating\s+systems?\b|\blinux\s+kernel\b",
    "DS&A / LeetCode": r"\bdata\s+structures\s+(?:and|&)\s+algorithms\b",
    "Software engineering": r"\bsoftware\s+(?:engineering|development)\s+(?:experience|skills|best\s+practices)",
}
DS_SKILLS = {
    "Python": r"\bpython\b", "R": r"(?<![\w-])R(?![\w-])", "SQL": r"\bsql\b", "Pandas": r"\bpandas\b",
    "NumPy": r"\bnumpy\b", "scikit-learn": r"\bscikit|\bsklearn\b", "Machine learning": r"\bmachine\s+learning\b|\bml\b",
    "Deep learning": r"\bdeep\s+learning\b|\bneural\s+net", "PyTorch": r"\bpytorch\b", "TensorFlow": r"\btensorflow\b",
    "Statistics": r"\bstatistic", "Regression": r"\bregression\b", "A/B testing": r"\ba/b\s+test|\bexperimentation\b",
    "Hypothesis testing": r"\bhypothesis\s+test", "Causal inference": r"\bcausal\b",
    "Time series / forecasting": r"\btime[\s-]series\b|\bforecast", "NLP": r"\bnlp\b|\bnatural\s+language",
    "LLM / GenAI": r"\bllms?\b|\bgenerative\s+ai\b|\bgenai\b", "Tableau": r"\btableau\b", "Power BI": r"\bpower\s?bi\b",
    "Looker": r"\blooker\b", "Excel": r"\bexcel\b", "Spark": r"\bspark\b|\bpyspark\b", "Airflow": r"\bairflow\b",
    "dbt": r"\bdbt\b", "Snowflake": r"\bsnowflake\b", "BigQuery": r"\bbigquery\b", "Databricks": r"\bdatabricks\b",
    "AWS": r"\baws\b", "GCP": r"\bgcp\b|\bgoogle\s+cloud\b", "Azure": r"\bazure\b", "ETL": r"\betl\b|\belt\b",
    "Data visualization": r"\bdata\s+visuali[sz]ation\b", "SAS": r"\bsas\b", "MATLAB": r"\bmatlab\b", "Git": r"\bgit\b",
}
LEARNABLE_DOMAINS = {
    "Finance": r"\bfinanc", "Accounting": r"\baccounting\b", "Marketing": r"\bmarketing\b",
    "Risk": r"\brisk\b", "Credit": r"\bcredit\b", "Fraud": r"\bfraud\b", "Probability": r"\bprobabilit",
    "Stochastic": r"\bstochastic\b", "Actuarial / insurance": r"\bactuar|\binsurance\b",
    "Pricing": r"\bpricing\b", "Supply chain": r"\bsupply\s+chain\b", "Healthcare": r"\bhealthcare\b|\bclinical\b",
    "Econometrics": r"\beconometric", "Product analytics": r"\bproduct\s+analytics\b", "CRM": r"\bcrm\b",
}


def _hits(table: dict[str, str], text: str) -> list[str]:
    flags = 0
    out = []
    for name, pat in table.items():
        f = flags if name == "R" else re.IGNORECASE
        if re.search(pat, text, f):
            out.append(name)
    return out


def check_skills_keywords(jd: str, resume: str, sde_block_threshold: int = 3) -> tuple[StepResult, int]:
    step = "4. Skills (keyword fallback)"
    jd_ds = _hits(DS_SKILLS, jd)
    have = set(_hits(DS_SKILLS, resume))
    jd_sde = _hits(SDE_SKILLS, jd)
    sde_have = set(_hits(SDE_SKILLS, resume))
    sde_missing = [s for s in jd_sde if s not in sde_have]
    domains = _hits(LEARNABLE_DOMAINS, jd)

    matched = [s for s in jd_ds if s in have]
    missing = [s for s in jd_ds if s not in have]
    score = round(100 * len(matched) / len(jd_ds)) if jd_ds else 50
    score = max(0, score - 12 * len(sde_missing))

    evidence = [
        f"Matched DS skills: {', '.join(matched) or '—'}",
        f"Missing DS skills: {', '.join(missing) or '—'}",
        f"SDE skills in JD you lack: {', '.join(sde_missing) or '—'}",
        f"Learnable domain topics (1–2 weeks): {', '.join(domains) or '—'}",
    ]
    if len(sde_missing) >= sde_block_threshold:
        return StepResult(step, FAIL, f"Looks like an SDE role ({len(sde_missing)} SWE skills you don't have).",
                          evidence), score
    status = PASS if score >= 60 else WARN if score >= 40 else FAIL
    return StepResult(step, status, f"Keyword match score {score}/100.", evidence), score
