"""Measure how accurate the screening is on hand-labelled real job postings.

    python evals/run.py                         # rules only (free)
    python evals/run.py --model claude-haiku-5-5 [--repeat 2]
    python evals/run.py --model claude-haiku-5-5 --model claude-sonnet-5-5 --write

For steps 1–3 the labels in labels.jsonl are ground truth. fit_score and difficulty have no
ground truth here (they depend on your resume), so the runner reports how much they move
between repeated runs of the same JD; a score that swings ±15 points can't drive a threshold.
Claude responses are cached in evals/runs/ so re-running costs nothing.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from resume_match import rules  # noqa: E402
from resume_match.rules import FAIL, PASS  # noqa: E402

GRAD = (2027, 12)
STEPS = ["citizenship_blocked", "sponsorship", "graduation_fit"]


def load():
    """JD texts aren't in git (they're the companies' content). Missing ones are re-fetched from
    their URL and cached in jds.jsonl; postings that have since closed are skipped."""
    path = HERE / "jds.jsonl"
    jds = {j["id"]: j for j in map(json.loads, path.read_text().splitlines())} if path.exists() else {}
    labels = [json.loads(l) for l in (HERE / "labels.jsonl").read_text().splitlines()]
    missing = [l for l in labels if l["id"] not in jds]
    if missing:
        from resume_match.fetcher import fetch_job
        with path.open("a", encoding="utf-8") as f:
            for l in missing:
                try:
                    text = fetch_job(l["url"]).text
                except Exception:  # noqa: BLE001 - a closed or unreadable posting just drops out
                    continue
                jds[l["id"]] = {"id": l["id"], "url": l["url"], "company": l["company"], "title": l["title"],
                                "text": text, "split": l["split"]}
                f.write(json.dumps(jds[l["id"]], ensure_ascii=False) + "\n")
    kept = [l for l in labels if l["id"] in jds]
    if len(kept) < len(labels):
        print(f"note: {len(labels) - len(kept)} labelled postings are closed and were skipped\n")
    return jds, kept


def rules_predict(jd: str) -> dict:
    s1 = rules.check_citizenship(jd)
    _, visa = rules.check_visa(jd)
    s3 = rules.check_graduation(jd, *GRAD)
    return {"citizenship_blocked": s1.status == FAIL, "sponsorship": visa,
            "graduation_fit": {PASS: "yes", FAIL: "no"}.get(s3.status, "unclear")}


def claude_predict(model: str, jd_id: str, jd: str, resume: str, run: int) -> dict:
    cache = HERE / "runs" / f"{model}.jsonl"
    cache.parent.mkdir(exist_ok=True)
    key = f"{jd_id}#{run}"
    if cache.exists():
        for line in cache.read_text().splitlines():
            row = json.loads(line)
            if row["key"] == key:
                return row
    from resume_match.llm import assess_with_usage, cost_usd
    rep, usage = assess_with_usage(jd, resume, grad_label="December 2027",
                                   visa_status="F-1 (CPT now, OPT / STEM OPT after graduation)",
                                   extra_requirements="", model=model)
    row = {"key": key, "citizenship_blocked": rep.citizenship_blocked, "sponsorship": rep.sponsorship,
           "graduation_fit": rep.graduation_fit, "fit_score": rep.fit_score, "difficulty": rep.difficulty,
           "technical_bar": rep.technical_bar, "input_tokens": usage.input_tokens,
           "output_tokens": usage.output_tokens, "cost": cost_usd(model, usage)}
    with cache.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def score(preds: dict[str, dict], labels: list[dict]) -> dict:
    out = {}
    for step in STEPS:
        ok = [preds[l["id"]][step] == l[step] for l in labels]
        out[step] = {"acc": sum(ok) / len(ok),
                     "misses": [(l["id"], l[step], preds[l["id"]][step], l["note"]) for l, k in zip(labels, ok) if not k]}
    tp = sum(preds[l["id"]]["citizenship_blocked"] and l["citizenship_blocked"] for l in labels)
    fp = sum(preds[l["id"]]["citizenship_blocked"] and not l["citizenship_blocked"] for l in labels)
    fn = sum(not preds[l["id"]]["citizenship_blocked"] and l["citizenship_blocked"] for l in labels)
    out["citizenship_blocked"]["recall"] = tp / (tp + fn) if tp + fn else 1.0
    out["citizenship_blocked"]["precision"] = tp / (tp + fp) if tp + fp else 1.0
    return out


def by_split(preds, labels, name):
    """Score the dev set (rules were tuned on it) and the held-out test set separately."""
    out = []
    for split in ("test", "dev"):
        part = [l for l in labels if l.get("split", "dev") == split]
        if part:
            tag = "held-out test" if split == "test" else "dev (rules tuned on these)"
            out += fmt(f"{name} — {tag}, n={len(part)}", score(preds, part))
    return out


def fmt(name: str, s: dict) -> list[str]:
    c = s["citizenship_blocked"]
    lines = [f"### {name}", "",
             "| step | accuracy | notes |", "|---|---|---|",
             f"| 1 citizenship | {c['acc']:.0%} | recall {c['recall']:.0%}, precision {c['precision']:.0%} |",
             f"| 2 sponsorship | {s['sponsorship']['acc']:.0%} | 4-way: h1b / opt_cpt / none_mentioned / no_sponsorship |",
             f"| 3 graduation | {s['graduation_fit']['acc']:.0%} | yes / no / unclear, for Dec 2027 |", ""]
    misses = [(st, *m) for st in STEPS for m in s[st]["misses"]]
    if misses:
        lines += ["Misses (step · id · label → predicted · why the label):", ""]
        lines += [f"- {st} · `{i}` · {lab} → {pred} · {note}" for st, i, lab, pred, note in misses]
        lines.append("")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", default=[], help="Claude model id; repeatable")
    ap.add_argument("--repeat", type=int, default=1, help="runs per JD, to measure score stability")
    ap.add_argument("--resume", default=str(HERE / "sample_resume.txt"))
    ap.add_argument("--write", action="store_true", help="write evals/RESULTS.md")
    a = ap.parse_args()

    jds, labels = load()
    dist = {st: Counter(str(l[st]) for l in labels) for st in STEPS}
    report = [f"# Eval results — {len(labels)} real postings", "",
              "Label counts: " + "; ".join(f"{st} {dict(c)}" for st, c in dist.items()), ""]

    rule_preds = {l["id"]: rules_predict(jds[l["id"]]["text"]) for l in labels}
    report += by_split(rule_preds, labels, "Rules (regex, free)")

    resume = Path(a.resume).read_text()
    for model in a.model:
        runs = [{l["id"]: claude_predict(model, l["id"], jds[l["id"]]["text"], resume, r) for l in labels}
                for r in range(a.repeat)]
        report += by_split(runs[0], labels, f"Claude `{model}`")
        dis = {st: sum(runs[0][l["id"]][st] != rule_preds[l["id"]][st] for l in labels) for st in STEPS}
        cost = statistics.mean(r["cost"] for r in runs[0].values())
        report += [f"Disagreement with rules: " + ", ".join(f"{st} {n}/{len(labels)}" for st, n in dis.items()),
                   f"Average cost per JD: ${cost:.4f} "
                   f"({statistics.mean(r['input_tokens'] for r in runs[0].values()):.0f} in / "
                   f"{statistics.mean(r['output_tokens'] for r in runs[0].values()):.0f} out tokens)", ""]
        if a.repeat > 1:
            for field in ("fit_score", "difficulty"):
                spread = [max(r[i][field] for r in runs) - min(r[i][field] for r in runs) for i in runs[0]]
                report.append(f"{field} spread across {a.repeat} runs: median {statistics.median(spread)}, "
                              f"max {max(spread)} points")
            report.append("")

    text = "\n".join(report)
    print(text)
    if a.write:
        (HERE / "RESULTS.md").write_text(text + "\n")


if __name__ == "__main__":
    main()
