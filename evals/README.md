# Evals

`labels.jsonl` holds 96 real postings from public Greenhouse / Lever boards, each labelled by hand for
steps 1–3 (citizenship, sponsorship, graduation fit for a Dec 2027 graduate). Each label comes with
a note saying why.

- **dev (66):** the set the regex rules were fixed against. Its scores are optimistic.
- **test (30):** different companies, labelled before any rule was run on them. This is the honest number.

```bash
python evals/run.py                                  # rules only, free
python evals/run.py --model claude-haiku-5-5 --repeat 2
python evals/run.py --model claude-haiku-5-5 --model claude-sonnet-5-5 --write   # → RESULTS.md
```

- **Claude runs:** each JD costs real API money, about $0.001 on Haiku. Responses are cached in `runs/`.
- **`--repeat 2`:** reports how far `fit_score` and `difficulty` move between two runs of the same JD. If they swing ±15, a 65-point threshold is noise.
- **`fit_score` accuracy:** there's no label for it, because it depends on your resume. To measure it, add `"should_apply": true/false` to the rows you've judged yourself and pass `--resume your_resume.txt`.

## Results so far (rules only)

| | citizenship | sponsorship | graduation |
|---|---|---|---|
| Before the eval (all 66 dev) | 91% (recall **50%**) | 88% | 92% |
| After fixes, dev | 100% | 100% | 98% |
| After fixes, **held-out test** | 97% (recall 100%) | 100% | 100% |

**What the first run caught:**
- "Must be a U.S. Person" didn't match, so half of the citizenship-blocked jobs slipped through.
- "opt out of recording" was read as OPT.
- "sponsorship for an export license" was read as visa sponsorship.
- An alumni sentence ("has graduated … since 2015") was taken as a graduation window.
- A date on the line after "On track to graduate by" was missed.

**Caveats:**
- The test set is small and has few visa / graduation cases.
- I labelled the rows by reading the relevant sentences. Spot-check them before trusting the numbers.

## Keeping Python and the web page in sync

All patterns and thresholds are in `resume_match/rules.json`. `scripts/build_web.py` inlines that file
into `web/index.html`, and `pytest` fails if the two copies differ. The matching logic exists twice
(Python and JS). Running both on all 96 postings gave identical predictions for steps 1–3, difficulty
and keyword skill scores.

## Adding postings

- `python evals/collect.py` adds to the dev set; `python evals/collect.py --test board1 board2` adds a test set from other companies.
- Then add a row per posting to `labels.jsonl`.
- JD texts stay local in `jds.jsonl` (gitignored). `run.py` re-fetches missing ones by URL and skips postings that have closed.
