from jobfit import rules
from jobfit.pipeline import APPLY, SKIP, screen
from jobfit.profile import DEFAULT_PROFILE
from jobfit.rules import FAIL, PASS, UNCLEAR, WARN


def status(r):
    return r[0].status if isinstance(r, tuple) else r.status


# ---------------- step 1
def test_citizenship_hits():
    for jd in [
        "Applicants must be U.S. citizens due to contract requirements.",
        "US Citizenship is required for this position.",
        "Must have an active Secret security clearance.",
        "This role requires permanent work authorization in the United States.",
        "Candidates must be a US citizen or permanent resident.",
        "This position is subject to ITAR and requires U.S. person status.",
    ]:
        assert status(rules.check_citizenship(jd)) == FAIL, jd


def test_citizenship_ignores_eeo_and_negation():
    for jd in [
        "We are an equal opportunity employer without regard to race, religion, or citizenship status.",
        "U.S. citizenship is not required for this role.",
        "Security clearance is not required.",
    ]:
        assert status(rules.check_citizenship(jd)) == PASS, jd


# ---------------- step 2
def test_visa():
    assert status(rules.check_visa("We are unable to sponsor visas for this role.")) == FAIL
    assert status(rules.check_visa("Must be authorized to work without sponsorship now or in the future.")) == FAIL
    assert status(rules.check_visa("We do not offer visa sponsorship.", reject_no_sponsorship=False)) == WARN
    assert rules.check_visa("H-1B sponsorship is available for this role.")[1] == "h1b"
    assert rules.check_visa("International students on F-1 OPT/CPT are welcome to apply.")[1] == "opt_cpt"
    assert status(rules.check_visa("Great benefits and snacks.")) == UNCLEAR


# ---------------- step 3  (candidate graduates Dec 2027)
def grad(jd):
    return rules.check_graduation(jd, 2027, 12).status


def test_graduation():
    assert grad("Candidates graduating between December 2026 and June 2027.") == FAIL
    assert grad("Expected graduation between May 2027 and December 2027.") == PASS
    assert grad("Must be graduating by June 2028.") == PASS
    assert grad("Must have graduated by May 2026.") == FAIL
    assert grad("Open to the Class of 2027.") == PASS
    assert grad("Open to the Class of 2026.") == FAIL
    assert grad("Must be returning to school after the internship.") == PASS
    assert grad("You love data.") == UNCLEAR


# ---------------- step 4 fallback + pipeline
RESUME = "Python, SQL, pandas, scikit-learn, statistics, A/B testing, Tableau, machine learning, AWS"


def test_sde_role_skipped():
    jd = ("Software Engineer Intern. Requirements: C++, Java, distributed systems, system design, "
          "Kubernetes, microservices. " * 3)
    profile = {**DEFAULT_PROFILE, "resume": RESUME, "use_llm": False}
    assert screen(jd, profile).decision == SKIP


def test_ds_role_applies():
    jd = ("Data Science Intern, credit risk team. We welcome international students on CPT. "
          "Requirements: Python, SQL, statistics, machine learning, A/B testing, Tableau. "
          "Knowledge of credit risk and probability is a plus.")
    profile = {**DEFAULT_PROFILE, "resume": RESUME, "use_llm": False}
    v = screen(jd, profile)
    assert v.decision == APPLY, v


def test_industry_does_not_matter_tech_stack_does():
    # "Supply Chain" title and logistics domain, but the daily tools are Python / SQL / dashboards.
    jd = ("Supply Chain Analyst Intern. Analyze logistics, inventory and transportation data to improve "
          "operations. Write SQL queries and Python scripts, and build Tableau dashboards for the supply chain team.")
    resume = "Data analytics projects with Python, pandas, SQL and Tableau dashboards."
    s4, score = rules.check_skills_keywords(jd, resume)
    assert s4.status == PASS and score >= 80
    assert any("Supply chain" in e and "not scored" in e for e in s4.evidence)


def test_ds_title_with_sde_stack_skipped():
    jd = ("Data Scientist. You will build backend microservices in Java and Go, deploy them on Kubernetes, "
          "and own system design for our distributed systems. Some Python.")
    s4, _ = rules.check_skills_keywords(jd, "Python, SQL, statistics, machine learning.")
    assert s4.status == FAIL


def test_citizenship_short_circuits():
    jd = "Data Scientist. Python, SQL. Must be a U.S. citizen. H-1B sponsorship is available."
    v = screen(jd, {**DEFAULT_PROFILE, "resume": RESUME}, api_key="should-not-be-used")
    assert v.decision == SKIP and len(v.steps) == 1


# ---------------- per-user config export / import
def test_profile_roundtrip_drops_unknown_keys():
    from jobfit.profile import profile_from_json, profile_to_json
    p = {**DEFAULT_PROFILE, "resume": "Python SQL", "grad_month": 3, "evil": "x"}
    back = profile_from_json(profile_to_json(p))
    assert back["resume"] == "Python SQL" and back["grad_month"] == 3 and "evil" not in back


# ---------------- multi-job comparison
def test_difficulty_heuristic():
    easy, _ = rules.estimate_difficulty("Data Science Intern. Python and SQL.", "Data Science Intern")
    hard, why = rules.estimate_difficulty("Requires 5+ years of experience. PhD required.", "Senior Data Scientist")
    assert easy < 40 < 80 <= hard and "PhD" in why


def test_rank_jobs_caps_and_skips():
    from jobfit.pipeline import MAYBE, Verdict, rank_jobs
    a = Verdict(APPLY, 85, "", difficulty=30, title="A")   # 73
    b = Verdict(APPLY, 90, "", difficulty=80, title="B")   # 58
    c = Verdict(MAYBE, 60, "", difficulty=10, title="C")   # 56
    d = Verdict(SKIP, 95, "", difficulty=0, title="D")     # hard fail: never recommended
    ranked = rank_jobs([d, c, b, a], max_apply=2)
    assert [v.title for v, _ in ranked] == ["A", "B", "C", "D"]
    assert [rec for _, rec in ranked] == [True, True, False, False]


# ---------------- H-1B filing history
def test_sponsor_history_rules():
    from jobfit.pipeline import apply_sponsor_history
    from jobfit.rules import StepResult
    from jobfit.sponsor import SponsorInfo, check_sponsor, guess_company
    silent = StepResult("2", UNCLEAR, "none")
    filed = SponsorInfo("Chime", filings=71, ds_filings=26, year=2026, employers=["CHIME FINANCIAL INC"])
    none = SponsorInfo("Tiny Co", filings=0)
    assert apply_sponsor_history(silent, "none_mentioned", filed).status == PASS
    assert apply_sponsor_history(silent, "none_mentioned", none).status == UNCLEAR
    # the JD saying "no sponsorship" beats any history
    no = StepResult("2", FAIL, "JD says no visa sponsorship.")
    assert apply_sponsor_history(no, "no_sponsorship", filed).status == FAIL
    # household names are recognised offline, legal suffixes and all
    assert check_sponsor("Amazon.com Services LLC", online=False).known_big
    assert check_sponsor("JPMorgan Chase & Co.", online=False).known_big
    assert not check_sponsor("Tiny Startup Inc", online=False).has_history
    assert guess_company("Role summary.\nAt Ramp, we build finance tools.") == "Ramp"
