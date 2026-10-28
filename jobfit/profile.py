"""Resume / settings / history handling.

Shared deployments keep everything in the viewer's own browser session (nothing
is written to the server). Disk persistence under data/ is only used in local
mode (JOBFIT_LOCAL=1), where the app has a single user.
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PROFILE_PATH = DATA_DIR / "profile.json"
HISTORY_PATH = DATA_DIR / "history.csv"

DEFAULT_PROFILE = {
    "resume": "",
    "visa_status": "F-1 (CPT now, OPT / STEM OPT after graduation)",
    "grad_year": 2027,
    "grad_month": 12,  # "2027 Winter" = December 2027 by default; change in the UI if you mean Jan–Mar 2027
    "reject_no_sponsorship": True,
    "use_llm": True,
    "apply_threshold": 65,
    "extra_requirements": "",
}

HISTORY_FIELDS = ["time", "verdict", "score", "company", "title", "url", "reason"]


def is_local_mode() -> bool:
    return os.environ.get("JOBFIT_LOCAL", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- profile

def normalize_profile(data: dict) -> dict:
    """Merge onto defaults and drop unknown keys (also used for imported files)."""
    return {k: data.get(k, v) for k, v in DEFAULT_PROFILE.items()}


def profile_to_json(profile: dict) -> str:
    return json.dumps(normalize_profile(profile), ensure_ascii=False, indent=2)


def profile_from_json(raw: bytes | str) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("config file must be a JSON object")
    return normalize_profile(data)


def load_profile() -> dict:
    if PROFILE_PATH.exists():
        try:
            return profile_from_json(PROFILE_PATH.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return dict(DEFAULT_PROFILE)


def save_profile(profile: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PROFILE_PATH.write_text(profile_to_json(profile))


def extract_resume_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if name.endswith(".docx"):
        import docx
        doc = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs).strip()
    return data.decode("utf-8", errors="ignore").strip()


# ---------------------------------------------------------------- history

def history_row(**fields) -> dict:
    return {"time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            **{k: fields.get(k, "") for k in HISTORY_FIELDS if k != "time"}}


def history_to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HISTORY_FIELDS)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def append_history(row: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    new = not HISTORY_PATH.exists()
    with HISTORY_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def load_history() -> list[dict]:
    """Newest first."""
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))[::-1]
