---
name: job-fit
description: Screen a job posting (URL or pasted JD) against the user's resume for an F-1 Data Science job seeker — rejects citizenship/clearance roles first, then checks OPT/CPT/H-1B, graduation date, and DS skill fit, and returns APPLY / MAYBE / SKIP. Use when the user pastes a job link or JD and asks whether to apply.
---

# Job fit screening

The user is an F-1 international student (CPT now, OPT / STEM OPT later) targeting Data Science /
Data Analyst / ML / quant-analytics roles. Expected graduation: **December 2027 ("2027 Winter")**
unless `data/profile.json` says otherwise. Their resume is in `data/profile.json` (`resume` field)
or a resume file in the project; read it first. If there is none, ask them to upload it.

## Getting the JD
1. Fetch the URL. For JS-heavy ATS pages, try the project's fetcher instead:
   `python -I -c "from jobfit.fetcher import fetch_job; print(fetch_job('<URL>').text)"` (run from the repo root).
2. If both fail (login wall, empty page), stop and ask the user to paste the JD text. Don't guess.

## Steps — run in order, stop at the first hard failure

**Step 1 — Citizenship (hard stop).** If the JD requires U.S. citizenship, green card / permanent
work authorization, a security clearance, or ITAR "U.S. person" status → verdict **SKIP**, quote the
line, and do not evaluate anything else. Ignore EEO boilerplate ("without regard to citizenship").

**Step 2 — Visa.** Classify: H-1B sponsorship offered (best) / OPT-CPT-F-1 welcome / not mentioned
(usually fine for CPT/OPT, flag H-1B as unknown) / explicitly no sponsorship now or in future
(→ SKIP unless the user's profile has `reject_no_sponsorship: false`).
When the JD is silent, check the company's H-1B filing history: run
`python -I -c "from jobfit.sponsor import check_sponsor; print(check_sponsor('<Company>').describe())"`
from the repo root (h1bdata.info + a list of large sponsors), or search MyVisaJobs / H1BGrader.
Any recent filings → the company can sponsor → treat step 2 as passed. A JD that explicitly says
"no sponsorship" still wins over history.

**Step 3 — Graduation.** Compare Dec 2027 against any graduation window, class year, "must have
graduated by", or "returning to school" requirement. Out of window → SKIP.

**Step 4 — Skills.** Read the requirements, not the title. Classify each skill:
- DS core (Python, SQL, stats, ML, experimentation, BI, cloud data tools) — check against resume.
- Learnable domain in 1–2 weeks (finance, marketing, risk, credit, fraud, probability, insurance,
  pricing…) — not a blocker.
- SDE out of scope (C++, Java/Go backend, distributed systems, system design, frontend/mobile,
  embedded, production software development as the core job) — a **required** one the user lacks → SKIP.

Give a fit score 0–100 (default apply threshold 65; MAYBE within 15 below).

## Output
```
Verdict: APPLY | MAYBE | SKIP   (Fit xx/100)
Company — Title
1. Citizenship: ✅/❌ + quote
2. Visa: ✅/⚠️/❌/❔ + quote
3. Graduation: ✅/❌/❔ + reasoning
4. Skills: table (skill | category | required | have | note)
总结 (2–3 sentences in Chinese) + 3–5 truthful resume tweaks
```
