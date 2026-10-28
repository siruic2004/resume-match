"""Fetch a job description from a URL.

Tries the public JSON APIs of common ATS platforms first (Greenhouse, Lever,
Ashby, Workday, LinkedIn guest view) because their pages are JS-rendered, then
falls back to plain HTML scraping. Raises FetchError when nothing usable comes
back, so the UI can ask the user to paste the JD manually.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 15
MIN_JD_CHARS = 400  # shorter than this is almost certainly a login wall / JS shell

BLOCK_MARKERS = (
    "enable javascript",
    "please turn on javascript",
    "sign in to view",
    "verify you are human",
    "access denied",
    "captcha",
)


class FetchError(Exception):
    pass


@dataclass
class JobPosting:
    text: str
    title: str = ""
    company: str = ""
    source: str = ""  # which strategy succeeded


def html_to_text(raw: str) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------- ATS adapters

def _greenhouse(u) -> JobPosting | None:
    if "greenhouse.io" not in u.netloc:
        return None
    parts = [p for p in u.path.split("/") if p]
    # boards.greenhouse.io/{board}/jobs/{id}  |  job-boards.greenhouse.io/{board}/jobs/{id}
    if "jobs" not in parts:
        return None
    i = parts.index("jobs")
    if i == 0 or i + 1 >= len(parts):
        return None
    board, job_id = parts[i - 1], parts[i + 1]
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}").json()
    text = html_to_text(html.unescape(data.get("content", "")))
    return JobPosting(text=text, title=data.get("title", ""), company=board, source="greenhouse-api")


def _lever(u) -> JobPosting | None:
    if "lever.co" not in u.netloc:
        return None
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        return None
    company, job_id = parts[0], parts[1]
    data = _get(f"https://api.lever.co/v0/postings/{company}/{job_id}").json()
    chunks = [data.get("descriptionPlain", "")]
    for lst in data.get("lists", []):
        chunks.append(lst.get("text", ""))
        chunks.append(html_to_text(lst.get("content", "")))
    chunks.append(data.get("additionalPlain", ""))
    return JobPosting(text="\n".join(c for c in chunks if c), title=data.get("text", ""),
                      company=company, source="lever-api")


def _ashby(u) -> JobPosting | None:
    if "ashbyhq.com" not in u.netloc:
        return None
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        return None
    company, job_id = parts[0], parts[1]
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{company}").json()
    for job in data.get("jobs", []):
        if job.get("id") == job_id:
            text = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml", ""))
            return JobPosting(text=text, title=job.get("title", ""), company=company, source="ashby-api")
    return None


def _workday(u) -> JobPosting | None:
    if "myworkdayjobs.com" not in u.netloc:
        return None
    tenant = u.netloc.split(".")[0]
    parts = [p for p in u.path.split("/") if p]
    if "job" not in parts:
        return None
    i = parts.index("job")
    if i == 0:
        return None
    site = parts[i - 1]  # path may start with a locale like en-US
    rest = "/".join(parts[i + 1:])
    api = f"https://{u.netloc}/wday/cxs/{tenant}/{site}/job/{rest}"
    data = _get(api).json().get("jobPostingInfo", {})
    text = html_to_text(data.get("jobDescription", ""))
    return JobPosting(text=text, title=data.get("title", ""), company=tenant, source="workday-api")


def _linkedin(u) -> JobPosting | None:
    if "linkedin.com" not in u.netloc:
        return None
    job_id = parse_qs(u.query).get("currentJobId", [None])[0]
    if not job_id:
        m = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)", u.path)
        job_id = m.group(1) if m else None
    if not job_id:
        return None
    raw = _get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}").text
    soup = BeautifulSoup(raw, "html.parser")
    desc = soup.select_one(".show-more-less-html__markup, .description__text")
    title = soup.select_one(".top-card-layout__title, h2")
    company = soup.select_one(".topcard__org-name-link, .topcard__flavor")
    criteria = soup.select_one(".description__job-criteria-list")
    text = html_to_text(str(desc)) if desc else ""
    if criteria:
        text += "\n" + html_to_text(str(criteria))
    return JobPosting(text=text,
                      title=title.get_text(strip=True) if title else "",
                      company=company.get_text(strip=True) if company else "",
                      source="linkedin-guest")


ADAPTERS = (_greenhouse, _lever, _ashby, _workday, _linkedin)


# ---------------------------------------------------------------- generic HTML

def _generic(url: str) -> JobPosting:
    resp = _get(url)
    soup = BeautifulSoup(resp.text, "html.parser")

    # Many career sites embed schema.org JobPosting JSON-LD (Google for Jobs).
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                org = item.get("hiringOrganization") or {}
                return JobPosting(
                    text=html_to_text(html.unescape(item.get("description", ""))),
                    title=item.get("title", ""),
                    company=org.get("name", "") if isinstance(org, dict) else "",
                    source="json-ld",
                )

    title = soup.title.get_text(strip=True) if soup.title else ""
    main = soup.find("main") or soup.find("article") or soup.body or soup
    return JobPosting(text=html_to_text(str(main)), title=title, source="html")


def _usable(posting: JobPosting | None) -> bool:
    if posting is None or len(posting.text) < MIN_JD_CHARS:
        return False
    head = posting.text[:1500].lower()
    return not any(m in head for m in BLOCK_MARKERS)


def fetch_job(url: str) -> JobPosting:
    url = url.strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    u = urlparse(url)
    errors = []

    for adapter in ADAPTERS:
        try:
            posting = adapter(u)
        except (requests.RequestException, ValueError, KeyError) as e:
            errors.append(f"{adapter.__name__}: {e}")
            continue
        if _usable(posting):
            return posting

    try:
        posting = _generic(url)
        if _usable(posting):
            return posting
        errors.append("page loaded but no job description found (JS-rendered or login wall)")
    except requests.RequestException as e:
        errors.append(str(e))

    raise FetchError("; ".join(errors) or "unknown error")
