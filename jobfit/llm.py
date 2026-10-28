"""Claude-based JD vs. resume assessment (steps 2–4, structured output)."""
from __future__ import annotations

from typing import Literal

import anthropic
from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-opus-5-5"

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
4. Skills: read the JD's actual requirements, not the job title. For every meaningful required or \
preferred skill, classify it:
   - core_ds: Python, SQL, statistics, ML, experimentation, BI/visualization, cloud data tools, etc.
   - learnable_domain: business/domain knowledge the candidate can pick up in 1–2 weeks \
(finance, marketing, risk, credit, fraud, probability, insurance, pricing, supply chain...).
   - sde_out_of_scope: software-engineering skills that cannot be faked in two weeks \
(C++, Java/Go backend, distributed systems, system design, frontend/mobile, embedded, low-latency, \
production software development as the main job).
   - other: anything else (degree level, years of experience, soft skills).
   Mark have=true only if the resume shows it (directly or by clearly equivalent experience).
   A REQUIRED sde_out_of_scope skill the candidate lacks is a blocker. Preferred/nice-to-have ones are not.
Then give fit_score 0–100 for how competitive this resume is for this JD (ignore visa), \
and list concrete resume tweaks that would raise the match (only truthful re-framing of existing experience)."""


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
    skills: list[SkillItem]
    sde_blockers: list[str] = Field(description="required out-of-scope SDE skills the candidate lacks")
    other_must_have_issues: list[str] = Field(description="violations of the candidate's extra must-have conditions")
    fit_score: int = Field(ge=0, le=100)
    summary: str = Field(description="2–3 sentences, in Chinese, on whether to apply and why")
    resume_tips: list[str]


def assess(jd: str, resume: str, *, grad_label: str, visa_status: str, extra_requirements: str,
           api_key: str, model: str = DEFAULT_MODEL) -> FitReport:
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
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"effort": "medium"},
        messages=[{"role": "user", "content": user}],
        output_format=FitReport,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    if response.parsed_output is None:
        raise RuntimeError(f"No structured output (stop_reason={response.stop_reason}).")
    return response.parsed_output
