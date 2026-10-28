"""Claude-based JD vs. resume assessment (steps 2–4, structured output)."""
from __future__ import annotations

from typing import Literal

import anthropic
from pydantic import BaseModel, Field

# Screening a JD is a high-volume classification task, so the cheapest model is the default.
# Per million tokens (input, output), USD. Run evals/run.py to compare accuracy before switching.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_EFFORT = "low"
# The report is ~1–2K tokens of JSON; the rest is headroom for thinking.
MAX_TOKENS = 6000

SYSTEM_PROMPT = """You screen job postings for an international F-1 student who wants Data Science / \
Data Analyst / ML / quantitative-analytics roles. Be strict and evidence-based: quote the JD.

Evaluate in this order:
1. Citizenship: does the JD require U.S. citizenship, green card / permanent work authorization, \
a security clearance, or ITAR "U.S. person" status? If yes, set citizenship_blocked=true.
2. Visa: classify sponsorship stance. "h1b" = sponsorship explicitly offered; "opt_cpt" = OPT/CPT/F-1 \
students explicitly welcome; "none_mentioned"; "no_sponsorship" = explicitly will not sponsor now or in \
the future.
3. Graduation: compare the candidate's expected graduation to any graduation window, class year, \
"returning to school", or "must have graduated by" requirement. "yes", "no", or "unclear".
4. Skills — judge the TECHNICAL BAR, not the industry and not the job title. \
For an intern / new grad, industry knowledge (supply chain, logistics, finance, marketing, risk, \
healthcare, insurance, ads, ...) is expected to be shallow and is learned on the job in 1–2 weeks: \
it never lowers the score and is never a blocker. What matters is the tool stack and technical depth \
of the daily work. A "Supply Chain Analyst" whose work is Python + SQL + dashboards has the same \
technical bar as a "Data Analyst" → a good match. A "Data Scientist" whose real work is building \
backend services in Java/Go is an SDE job → not a match. Titles mislead in both directions; ignore them.
   First list tech_stack: the tools and techniques the job actually uses day to day (from \
responsibilities and requirements). Then set technical_bar by comparing that stack and its depth with \
the resume: "matches" = same kind of tools at a depth the resume already shows; "stretch" = same kind \
of tools but deeper (e.g. production ML, advanced causal inference, large-scale Spark) or one or two \
missing tools the candidate can pick up quickly; "above" = the core work needs technical depth the \
resume is far from (research-level modeling, PhD-level methods, heavy engineering).
   For every meaningful required or preferred skill, classify it:
   - core_ds: data tools and methods — Python, R, SQL, Excel, statistics, ML, experimentation, \
BI/dashboards (Tableau, Power BI, Looker), ETL, cloud data tools, optimization/forecasting, etc.
   - learnable_domain: industry / business knowledge of any kind (finance, marketing, risk, supply chain, \
logistics, operations, healthcare, probability concepts...). Never a blocker.
   - sde_out_of_scope: software-engineering skills that cannot be faked in two weeks \
(C++, Java/Go backend, distributed systems, system design, frontend/mobile, embedded, low-latency, \
production software development as the main job).
   - other: anything else (degree level, years of experience, soft skills).
   Mark have=true only if the resume shows it (directly or by clearly equivalent experience).
   A REQUIRED sde_out_of_scope skill the candidate lacks is a blocker. Preferred/nice-to-have ones are not.
Then give fit_score 0–100 for how competitive this resume is for this JD (ignore visa), based on \
tech-stack overlap and technical depth only, never on industry match; \
and difficulty 0–100 for how hard it is for THIS candidate to get an interview \
(seniority, years of experience, degree level, how selective the company/team is, how many \
nice-to-haves they lack; 0 = easy, 100 = very hard). \
Finally list concrete resume tweaks that would raise the match (only truthful re-framing of existing experience)."""


class SkillItem(BaseModel):
    skill: str
    category: Literal["core_ds", "learnable_domain", "sde_out_of_scope", "other"]
    required: bool = Field(description="true if listed as required/minimum, false if preferred/nice-to-have")
    have: bool
    note: str = Field(description="short evidence from resume, or how to close the gap")


class FitReport(BaseModel):
    job_title: str
    company: str
    citizenship_blocked: bool
    citizenship_evidence: str
    sponsorship: Literal["h1b", "opt_cpt", "none_mentioned", "no_sponsorship"]
    sponsorship_evidence: str
    graduation_fit: Literal["yes", "no", "unclear"]
    graduation_reasoning: str
    tech_stack: list[str] = Field(description="tools / techniques the job actually uses day to day")
    technical_bar: Literal["matches", "stretch", "above"]
    technical_bar_reason: str = Field(description="one short sentence, in Chinese")
    skills: list[SkillItem]
    sde_blockers: list[str] = Field(description="required out-of-scope SDE skills the candidate lacks")
    other_must_have_issues: list[str] = Field(description="violations of the candidate's extra must-have conditions")
    fit_score: int = Field(ge=0, le=100)
    difficulty: int = Field(ge=0, le=100, description="how hard to get an interview, 0 easy – 100 very hard")
    difficulty_reason: str = Field(description="one short sentence, in Chinese")
    summary: str = Field(description="2–3 sentences, in Chinese, on whether to apply and why")
    resume_tips: list[str]


def cost_usd(model: str, usage) -> float:
    price_in, price_out = PRICES.get(model, PRICES["claude-opus-5-5"])
    return (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1_000_000


def assess(jd: str, resume: str, **kwargs) -> FitReport:
    return assess_with_usage(jd, resume, **kwargs)[0]


def assess_with_usage(jd: str, resume: str, *, grad_label: str, visa_status: str, extra_requirements: str,
                      api_key: str = "", model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT):
    """Returns (FitReport, response.usage)."""
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    user = f"""<candidate>
Visa status: {visa_status}
Expected graduation: {grad_label}
Extra must-have conditions from the candidate: {extra_requirements or "none"}
</candidate>

<resume>
{resume}
</resume>

<job_description>
{jd}
</job_description>"""

    response = client.messages.parse(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        messages=[{"role": "user", "content": user}],
        output_format=FitReport,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    if response.parsed_output is None:
        raise RuntimeError(f"No structured output (stop_reason={response.stop_reason}).")
    return response.parsed_output, response.usage
