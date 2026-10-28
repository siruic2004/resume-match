"""Has this company filed H-1B petitions before?

MyVisaJobs and H1BGrader sit behind a bot check, so the automatic lookup uses
h1bdata.info, which serves the same public Department of Labor LCA filings.
The other two are returned as links for a manual double-check. Household-name
companies that sponsor at scale are recognised from a built-in list without a
network call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from .fetcher import HEADERS

# Large, well-known employers that file H-1B petitions every year.
KNOWN_SPONSORS = {
    # tech
    "amazon", "aws", "google", "alphabet", "microsoft", "meta", "facebook", "apple", "netflix", "nvidia",
    "intel", "ibm", "oracle", "salesforce", "adobe", "cisco", "qualcomm", "amd", "linkedin", "uber",
    "lyft", "airbnb", "doordash", "instacart", "stripe", "paypal", "block", "square", "snowflake",
    "databricks", "servicenow", "workday", "intuit", "vmware", "broadcom", "dell", "hp", "tiktok",
    "bytedance", "snap", "pinterest", "spotify", "ebay", "walmart", "target", "expedia", "booking",
    "wayfair", "twilio", "atlassian", "palantir", "tesla", "samsung", "visa", "mastercard",
    # finance
    "jpmorgan", "jpmorgan chase", "goldman sachs", "morgan stanley", "citi", "citigroup", "citibank",
    "bank of america", "wells fargo", "capital one", "american express", "blackrock", "barclays",
    "deutsche bank", "ubs", "hsbc", "fidelity", "vanguard", "charles schwab", "two sigma", "citadel",
    "jane street", "de shaw", "d e shaw", "bloomberg", "s&p global", "moodys", "discover",
    # consulting / services
    "deloitte", "pwc", "pricewaterhousecoopers", "ey", "ernst young", "kpmg", "accenture", "mckinsey",
    "boston consulting group", "bcg", "bain", "cognizant", "infosys", "tata consultancy services", "tcs",
    "wipro", "hcl", "capgemini", "zs associates",
    # healthcare / other large
    "pfizer", "johnson johnson", "unitedhealth", "cvs", "optum", "merck", "abbvie", "genentech",
    "eli lilly", "walgreens", "humana", "geico", "progressive", "state farm",
}

SUFFIXES = r"\b(?:inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|plc|lp|llp|na|n\.a|group|holdings|the|usa|us|america|technologies|technology)\b"
DS_TITLE = re.compile(r"data|analyst|analytics|scien|machine learning|\bml\b|quant|statistic|research", re.I)


@dataclass
class SponsorInfo:
    company: str
    known_big: bool = False
    filings: int | None = None       # H-1B LCA rows found on h1bdata.info
    ds_filings: int | None = None    # of which data / analytics / science titles
    year: int | None = None
    employers: list[str] = field(default_factory=list)  # matched legal names
    error: str = ""
    links: dict[str, str] = field(default_factory=dict)

    @property
    def has_history(self) -> bool:
        return self.known_big or bool(self.filings)

    def describe(self) -> str:
        if self.filings:
            ds = f"，其中 Data/Analytics/Science 相关 {self.ds_filings} 条" if self.ds_filings else ""
            names = "、".join(self.employers[:3])
            return f"h1bdata.info：{self.year} 年有 {self.filings} 条 H-1B 申请记录{ds}（{names}）"
        if self.known_big:
            return f"{self.company} 是常年大量办 H-1B 的大公司"
        if self.error:
            return f"没查到 {self.company} 的 H-1B 记录（{self.error}），请点链接人工核实"
        return f"h1bdata.info 近两年没有 {self.company} 的 H-1B 记录（也可能公司法定名称不同），请点链接核实"


def normalize(name: str) -> str:
    n = name.lower().replace("&", " ").replace("'", "")
    n = re.sub(r"[.,()/\-]", " ", n)
    n = re.sub(SUFFIXES, " ", n)
    return re.sub(r"\s+", " ", n).strip()


def lookup_links(company: str) -> dict[str, str]:
    q = quote_plus(company)
    return {
        "MyVisaJobs": f"https://www.myvisajobs.com/employers/search.aspx?e={q}",
        "MyVisaJobs E-Verify (STEM OPT)": f"https://www.myvisajobs.com/employers/e-verify.aspx?e={q}",
        "H1BGrader": "https://h1bgrader.com/",
        "h1bdata": f"https://h1bdata.info/index.php?em={q}&job=&city=&year=all+years",
    }


def _h1bdata(name: str, year: int) -> list[tuple[str, str]]:
    url = f"https://h1bdata.info/index.php?em={quote_plus(name)}&job=&city=&year={year}"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", id="myTable") or soup.find("table")
    rows = []
    for tr in table.find_all("tr")[1:] if table else []:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) >= 2:
            rows.append((cells[0], cells[1]))  # employer, job title
    return rows


@lru_cache(maxsize=256)
def check_sponsor(company: str, online: bool = True) -> SponsorInfo:
    company = company.strip()
    info = SponsorInfo(company=company, links=lookup_links(company))
    norm = normalize(company)
    if not norm:
        info.error = "公司名为空"
        return info
    info.known_big = any(norm == k or norm.startswith(k + " ") for k in KNOWN_SPONSORS)
    if info.known_big or not online:
        return info

    # Full name first; then its first word ("amazon com services" -> "amazon") if distinctive enough.
    queries = [norm]
    first = norm.split()[0]
    if first != norm and len(first) >= 5 and first not in GENERIC_FIRST_WORDS:
        queries.append(first)

    this_year = date.today().year
    try:
        for q in queries:
            for year in (this_year, this_year - 1):
                # h1bdata matches by prefix; keep rows whose employer really starts with the query
                rows = [r for r in _h1bdata(q, year) if (normalize(r[0]) + " ").startswith(q + " ")]
                if rows:
                    info.filings, info.year = len(rows), year
                    info.ds_filings = sum(bool(DS_TITLE.search(t)) for _, t in rows)
                    info.employers = list(dict.fromkeys(e for e, _ in rows))
                    return info
        info.filings = 0
    except requests.RequestException as e:
        info.error = f"h1bdata 查询失败: {type(e).__name__}"
    return info


GENERIC_FIRST_WORDS = {"first", "american", "national", "united", "global", "general", "international",
                       "capital", "digital", "health", "advanced", "applied", "central", "pacific", "north",
                       "south", "western", "eastern", "great", "smart", "data", "cloud", "financial"}


COMPANY_PATTERNS = [
    re.compile(r"\bAt ([A-Z][\w&.\-]*(?: [A-Z][\w&.\-]*){0,3}),? (?:we|our)\b"),
    re.compile(r"\bAbout ([A-Z][\w&.\-]*(?: [A-Z][\w&.\-]*){0,3})\s*\n"),
    re.compile(r"\b([A-Z][\w&.\-]*(?: [A-Z][\w&.\-]*){0,3}) is an equal opportunity employer"),
]


def guess_company(jd: str) -> str:
    for pat in COMPANY_PATTERNS:
        m = pat.search(jd)
        if m and m.group(1).lower() not in {"the", "we", "our", "this", "the company"}:
            return m.group(1)
    return ""
