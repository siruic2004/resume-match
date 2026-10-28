"""Inline resume_match/rules.json into web/index.html (the web page can't fetch other files).

Run after editing rules.json:  python scripts/build_web.py
tests/test_rules.py fails if the two copies drift apart.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "resume_match" / "rules.json"
PAGE = ROOT / "web" / "index.html"
BLOCK = re.compile(r'(<script type="application/json" id="rules-data">)(.*?)(</script>)', re.S)


def inline_json() -> str:
    data = json.loads(RULES.read_text(encoding="utf-8"))
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def main() -> None:
    page = PAGE.read_text(encoding="utf-8")
    if not BLOCK.search(page):
        raise SystemExit(f"no rules-data block in {PAGE}")
    page = BLOCK.sub(lambda m: m.group(1) + inline_json() + m.group(3), page, count=1)
    PAGE.write_text(page, encoding="utf-8")
    print(f"inlined {RULES.name} into {PAGE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
