# 🎯 JobFit — 投不投，一键判断

A Google-style search bar for job descriptions. Paste a JD link, and JobFit screens it against your
resume for an **F-1 international student targeting Data Science roles**:

| Step | Check | On failure |
|---|---|---|
| 1 | U.S. citizenship / green card / permanent work authorization / security clearance / ITAR | **Stop immediately** (no API call) |
| 2 | OPT / CPT accepted, H-1B sponsorship (bonus) | SKIP if "no sponsorship" (toggle) |
| 3 | Expected graduation (default **Dec 2027**) inside the JD's window | SKIP |
| 4 | Skills from the JD body (not the title): DS core vs. learnable domain vs. SDE out-of-scope | SKIP if required SDE skills / low score |

Result: **APPLY / MAYBE / SKIP**, a fit score, quoted evidence, a skill table, and resume tweaks.

## Features
- **URL fetch** with native support for Greenhouse, Lever, Ashby, Workday, LinkedIn (guest view)
  and any site with schema.org `JobPosting` data. If the page can't be read, a **manual paste box** opens.
- **Resume panel** — upload PDF/DOCX/TXT or paste.
- **Requirements panel** — visa status, graduation date, "no sponsorship = reject" toggle, apply
  threshold, and free-text extra must-haves (location, internship only, …).
- **Claude analysis** (Opus 5.5 by default) with a rules-only fallback when no API key is set.
- **History** of every job you screened, downloadable as CSV.

## Two modes

| | Shared mode (default) | Local mode (`JOBFIT_LOCAL=1`) |
|---|---|---|
| Who | Friends using your deployed link | Just you, on your own computer |
| Resume & settings | Only in the visitor's browser tab — never written to the server, invisible to others. Saved via **💾 配置 → 导出 / 导入** | Auto-saved to `data/profile.json` |
| History | Per tab, download as CSV | Also appended to `data/history.csv` |
| API key | Each visitor pastes **their own** key (billed to them) | Read from `.streamlit/secrets.toml` / `ANTHROPIC_API_KEY` |

The owner's key is **ignored in shared mode**, so a public deployment can never bill you for
other people's usage.

## Run locally
```bash
git clone https://github.com/siruic2004/resume-match.git && cd resume-match
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add your ANTHROPIC_API_KEY
JOBFIT_LOCAL=1 streamlit run app.py
```

## Deploy free on Streamlit Community Cloud
1. Push this repo to GitHub (`data/` and `secrets.toml` are git-ignored).
2. share.streamlit.io → sign in with GitHub → **Create app** → pick the repo, main file `app.py`.
3. **Don't** add an API key in Secrets and **don't** set `JOBFIT_LOCAL` — friends bring their own key.
4. Share the `*.streamlit.app` link. Each visitor exports their config once and imports it next time.

## Use as a Claude Code skill (no UI, no API key)
Copy `skill/job-fit/` into `~/.claude/skills/` and say *"这个岗位适合我吗 <url>"* in Claude Code.

## Project layout
```
app.py               Streamlit UI
jobfit/fetcher.py    URL → JD text (ATS APIs, JSON-LD, HTML)
jobfit/rules.py      Regex screening for steps 1–3 + keyword skill fallback
jobfit/llm.py        Claude structured-output assessment
jobfit/pipeline.py   Step ordering, short-circuit, final verdict
jobfit/profile.py    Per-visitor profile, config export/import, local-mode storage
tests/               Rule tests (pytest)
```

## Tuning
Edit the regex lists in `jobfit/rules.py` (e.g. add phrases you see in the wild) and the skill
categories in `SYSTEM_PROMPT` in `jobfit/llm.py`. Run `pytest` after changes.
