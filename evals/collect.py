"""Pull real job postings from public Greenhouse / Lever boards into evals/jds.jsonl.

    python evals/collect.py                    # dev set: the BOARDS / VISA_BOARDS lists below
    python evals/collect.py --test b1 b2 ...   # held-out test set from other Greenhouse boards

Labels are added separately in evals/labels.jsonl (see evals/README.md).
"""
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from resume_match.fetcher import HEADERS  # noqa: E402

OUT = Path(__file__).with_name("jds.jsonl")
TITLE = re.compile(r"data|analyst|analytics|scien|machine learning|quant|intern|new grad|software engineer", re.I)

# (ats, board, max postings). Defense / aerospace boards supply citizenship and clearance cases.
BOARDS = [
    ("greenhouse", "andurilindustries", 5), ("greenhouse", "spacex", 4), ("greenhouse", "shieldai", 3),
    ("greenhouse", "stripe", 4), ("greenhouse", "robinhood", 4), ("greenhouse", "databricks", 3),
    ("greenhouse", "airbnb", 3), ("greenhouse", "chime", 3), ("greenhouse", "instacart", 3),
    ("greenhouse", "point72", 3), ("greenhouse", "sofi", 3), ("greenhouse", "affirm", 3),
    ("lever", "palantir", 4), ("lever", "plaid", 3),
]
# Second pass: only postings that talk about visas, so step 2 has enough positive and negative cases.
VISA = re.compile(r"sponsor|visa|\bopt\b|\bcpt\b|f-1|work authori", re.I)
VISA_BOARDS = ["robinhood", "affirm", "brex", "figma", "discord", "roblox", "duolingo", "lyft", "pinterest",
               "reddit", "gusto", "datadog", "mongodb", "cloudflare", "twilio", "okta", "dropbox", "asana",
               "squarespace", "flexport", "opendoor", "doordashusa", "coinbase", "nuro", "waymo", "scaleai"]


def text_of(raw_html: str) -> str:
    return BeautifulSoup(html.unescape(raw_html or ""), "html.parser").get_text("\n", strip=True)


def greenhouse(board: str):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true", headers=HEADERS, timeout=30)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        yield f"gh-{board}-{j['id']}", j["absolute_url"], board, j["title"], text_of(j.get("content", ""))


def lever(board: str):
    r = requests.get(f"https://api.lever.co/v0/postings/{board}?mode=json", headers=HEADERS, timeout=30)
    r.raise_for_status()
    for j in r.json():
        body = text_of(j.get("description", "")) + "\n" + "\n".join(
            l.get("text", "") + "\n" + text_of(l.get("content", "")) for l in j.get("lists", [])
        ) + "\n" + text_of(j.get("additional", ""))
        yield f"lv-{board}-{j['id']}", j["hostedUrl"], board, j["text"], body


def main():
    test = sys.argv[1:2] == ["--test"]
    saved = [json.loads(l) for l in OUT.read_text().splitlines()] if OUT.exists() else []
    seen = {j["id"] for j in saved}
    per_board = Counter(j["company"] for j in saved)
    added = 0
    if test:
        plan = [("greenhouse", b, 3, None) for b in sys.argv[2:]]
    else:
        plan = [(ats, b, n, None) for ats, b, n in BOARDS] + [("greenhouse", b, 2, VISA) for b in VISA_BOARDS]
    with OUT.open("a", encoding="utf-8") as f:
        for ats, board, limit, need in plan:
            try:
                got = per_board[board]
                for jid, url, company, title, text in (greenhouse if ats == "greenhouse" else lever)(board):
                    if got >= limit or jid in seen or not TITLE.search(title) or len(text) < 400 \
                            or (need and not need.search(text)):
                        continue
                    f.write(json.dumps({"id": jid, "url": url, "company": company, "title": title, "text": text,
                                        "split": "test" if test else "dev"},
                                       ensure_ascii=False) + "\n")
                    got += 1
                    added += 1
                print(f"{board}: {got - per_board[board]} new")
            except requests.RequestException as e:
                print(f"{board}: failed ({type(e).__name__})")
    print(f"added {added}")


if __name__ == "__main__":
    main()
