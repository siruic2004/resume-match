"""Runs the four screening steps in order and produces a final verdict.

Step 1 (citizenship) short-circuits: if the rules catch it, nothing else runs
and no API call is made.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

from . import rules
from .rules import FAIL, PASS, UNCLEAR, WARN, StepResult
from .sponsor import SponsorInfo, check_sponsor, guess_company

APPLY, MAYBE, SKIP = "APPLY", "MAYBE", "SKIP"


@dataclass
class Verdict:
    decision: str
    score: int | None
    reason: str
    steps: list[StepResult] = field(default_factory=list)
    report: object | None = None  # llm.FitReport when Claude was used
    title: str = ""
    company: str = ""
    difficulty: int | None = None  # 0 easy – 100 very hard to get an interview
    difficulty_reason: str = ""
    sponsor: SponsorInfo | None = None  # company H-1B filing history


def grad_label(profile: dict) -> str:
    return f"{calendar.month_name[profile['grad_month']]} {profile['grad_year']}"


def _skip(steps, reason, **kw) -> Verdict:
    return Verdict(SKIP, kw.pop("score", None), reason, steps, **kw)


def screen(jd: str, profile: dict, *, api_key: str = "", model: str = "", company: str = "",
           title: str = "") -> Verdict:
    """`company` (from the job page, or typed by the user) is used to look up H-1B history;
    without it the name comes from Claude's reading of the JD or a regex guess."""
    v = _screen(jd, profile, api_key=api_key, model=model, company=company)
    if v.report is not None:
        v.difficulty, v.difficulty_reason = v.report.difficulty, v.report.difficulty_reason
    else:
        v.difficulty, v.difficulty_reason = rules.estimate_difficulty(jd, v.title or title)
    return v


# Fit counts more than difficulty: a strong match at a hard team still beats a weak match at an easy one.
DIFFICULTY_WEIGHT = 0.4


def priority(v: Verdict) -> float:
    return (v.score or 0) - DIFFICULTY_WEIGHT * (v.difficulty or 0)


def rank_jobs(verdicts: list[Verdict], max_apply: int) -> list[tuple[Verdict, bool]]:
    """Order jobs for a company that caps applications. Hard SKIPs go last and are
    never recommended; the top `max_apply` of the rest are marked recommended."""
    order = {APPLY: 0, MAYBE: 1, SKIP: 2}
    ranked = sorted(verdicts, key=lambda v: (v.decision == SKIP, -priority(v), order[v.decision]))
    out, picked = [], 0
    for v in ranked:
        rec = v.decision != SKIP and picked < max_apply
        picked += rec
        out.append((v, rec))
    return out


def sponsor_lookup(company: str, profile: dict) -> SponsorInfo | None:
    if not profile.get("check_h1b_history", True) or not company.strip():
        return None
    return check_sponsor(company.strip())


def apply_sponsor_history(s2: StepResult, label: str, info: SponsorInfo | None) -> StepResult:
    """Fold the company's H-1B filing history into the visa step. A JD that says
    "no sponsorship" still wins; history upgrades a JD that says nothing."""
    if info is None:
        return s2
    evidence = [info.describe()] + s2.evidence
    if label == "no_sponsorship":
        note = "（公司虽有 H-1B 记录，但这个岗位写明不 sponsor）" if info.has_history else ""
        return StepResult(s2.step, s2.status, s2.summary + note, evidence)
    if label == "none_mentioned":
        if info.has_history:
            return StepResult(s2.step, PASS, "JD 没提签证，但公司有 H-1B 申请记录 → 可以 sponsor", evidence)
        return StepResult(s2.step, UNCLEAR, "JD 没提签证，也没查到公司的 H-1B 记录，请点链接核实", evidence)
    return StepResult(s2.step, s2.status, s2.summary, evidence)


def _screen(jd: str, profile: dict, *, api_key: str = "", model: str = "", company: str = "") -> Verdict:
    steps: list[StepResult] = []

    # ---- 1. citizenship: hard stop, before anything else
    s1 = rules.check_citizenship(jd)
    steps.append(s1)
    if s1.status == FAIL:
        return _skip(steps, "需要美国公民 / 绿卡 / 安全许可 — 直接放弃")

    # ---- 2 & 3. rules pass
    s2, visa_label = rules.check_visa(jd, profile["reject_no_sponsorship"])
    s3 = rules.check_graduation(jd, profile["grad_year"], profile["grad_month"])
    steps += [s2, s3]

    resume = profile.get("resume", "").strip()
    use_llm = profile.get("use_llm", True) and bool(api_key)

    if not resume or not use_llm:
        info = sponsor_lookup(company or guess_company(jd), profile)
        steps[1] = apply_sponsor_history(s2, visa_label, info)

    if not resume:
        for i, s in enumerate(steps, 1):
            if s.status == FAIL:
                return _skip(steps, f"Step {i} 未通过 — {s.summary}", sponsor=info)
        return Verdict(MAYBE, None, "前三步通过；上传简历后才能做技能匹配", steps, sponsor=info)

    if not use_llm:
        s4, score = rules.check_skills_keywords(jd, resume or "")
        steps.append(s4)
        v = _decide(steps, score, profile)
        v.sponsor = info
        return v

    # ---- Claude pass (steps 1–4 with judgement; rules above stay as a cross-check)
    from .llm import DEFAULT_MODEL, assess
    rep = assess(jd, resume, grad_label=grad_label(profile), visa_status=profile["visa_status"],
                 extra_requirements=profile.get("extra_requirements", ""), api_key=api_key,
                 model=model or DEFAULT_MODEL)

    if rep.citizenship_blocked:
        steps[0] = StepResult(s1.step, FAIL, "Claude found a citizenship / clearance requirement.",
                              [rep.citizenship_evidence])
        return _skip(steps, "需要美国公民 / 绿卡 / 安全许可 — 直接放弃", report=rep,
                     title=rep.job_title, company=rep.company)

    visa_status = {"h1b": PASS, "opt_cpt": PASS, "none_mentioned": UNCLEAR,
                   "no_sponsorship": FAIL if profile["reject_no_sponsorship"] else WARN}[rep.sponsorship]
    # the stricter of rules vs. Claude wins
    if s2.status == FAIL:
        visa_status = FAIL
    steps[1] = StepResult(s2.step, visa_status, f"Claude: {rep.sponsorship}. {s2.summary}",
                          [rep.sponsorship_evidence] + s2.evidence)
    info = sponsor_lookup(company or rep.company or guess_company(jd), profile)
    steps[1] = apply_sponsor_history(steps[1], "no_sponsorship" if visa_status == FAIL else rep.sponsorship, info)

    grad_status = {"yes": PASS, "no": FAIL, "unclear": UNCLEAR}[rep.graduation_fit]
    if s3.status == FAIL and grad_status != PASS:
        grad_status = FAIL
    steps[2] = StepResult(s3.step, grad_status, rep.graduation_reasoning, s3.evidence)

    blockers = rep.sde_blockers
    s4_status = FAIL if blockers else (PASS if rep.fit_score >= profile["apply_threshold"] else WARN)
    s4_summary = (f"超纲 SDE 技能: {', '.join(blockers)}" if blockers else f"Fit score {rep.fit_score}/100")
    steps.append(StepResult("4. Skills & experience (Claude)", s4_status, s4_summary, rep.other_must_have_issues))

    v = _decide(steps, rep.fit_score, profile)
    v.report, v.title, v.company, v.sponsor = rep, rep.job_title, rep.company, info
    if rep.other_must_have_issues and v.decision == APPLY:
        v.decision, v.reason = MAYBE, "不满足你的额外必备条件: " + "; ".join(rep.other_must_have_issues)
    return v


def _decide(steps: list[StepResult], score: int, profile: dict) -> Verdict:
    labels = {1: "需要公民身份", 2: "不提供签证支持 / 不接受 OPT·CPT", 3: "毕业时间不符", 4: "技能超纲或匹配度过低"}
    for i, s in enumerate(steps, 1):
        if s.status == FAIL:
            return _skip(steps, f"Step {i}: {labels[i]} — {s.summary}", score=score)
    t = profile["apply_threshold"]
    if score >= t and not any(s.status == WARN for s in steps):
        return Verdict(APPLY, score, "通过全部筛选，建议投递", steps)
    if score >= t - 15:
        return Verdict(MAYBE, score, "基本符合，但有需要人工确认的地方", steps)
    return Verdict(SKIP, score, f"匹配度 {score} 低于阈值 {t}", steps)
