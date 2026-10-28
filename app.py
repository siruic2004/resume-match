"""JobFit — paste a JD URL, get an apply / skip verdict against your resume.

Run:  streamlit run app.py                  (shared mode: per-visitor data, bring-your-own key)
      JOBFIT_LOCAL=1 streamlit run app.py   (local mode: saves to data/, reads ANTHROPIC_API_KEY)
"""
import calendar
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from jobfit.fetcher import FetchError, fetch_job
from jobfit.pipeline import APPLY, DIFFICULTY_WEIGHT, MAYBE, SKIP, priority, rank_jobs, screen
from jobfit.profile import (DEFAULT_PROFILE, append_history, extract_resume_text, history_row,
                            history_to_csv, is_local_mode, load_history, load_profile,
                            profile_from_json, profile_to_json, save_profile)
from jobfit.rules import FAIL, PASS, UNCLEAR, WARN
from jobfit.sponsor import check_sponsor

st.set_page_config(page_title="JobFit", page_icon="🎯", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 2rem;}
.jf-logo {text-align:center; font-size:3rem; font-weight:700; letter-spacing:-1px; margin-bottom:0;}
.jf-logo span:nth-child(1){color:#4285F4}.jf-logo span:nth-child(2){color:#EA4335}
.jf-logo span:nth-child(3){color:#FBBC05}.jf-logo span:nth-child(4){color:#34A853}
.jf-sub {text-align:center; color:#888; margin-top:-.4rem; margin-bottom:1.2rem;}
div[data-testid="stTextInput"] input {border-radius:24px; padding:.8rem 1.2rem; font-size:1.05rem;}
.jf-verdict {border-radius:14px; padding:1rem 1.4rem; margin:.6rem 0 1rem; color:white;}
.jf-verdict h2 {margin:0; color:white;}
</style>
<div class="jf-logo"><span>Job</span><span>F</span><span>i</span><span>t</span></div>
<div class="jf-sub">粘贴 JD 链接 → 公民身份 → 签证 → 毕业时间 → 技能匹配</div>
""", unsafe_allow_html=True)

LOCAL = is_local_mode()
ss = st.session_state
ss.setdefault("fetch_failed", False)
ss.setdefault("fetch_error", "")
ss.setdefault("result", None)
ss.setdefault("history", load_history() if LOCAL else [])

# Each visitor gets their own copy of the profile in session state. Widget keys
# map 1:1 to profile fields so an imported config can repopulate the form.
WIDGET_KEYS = {"resume": "p_resume", "visa_status": "p_visa", "grad_year": "p_grad_year",
               "grad_month": "p_grad_month", "reject_no_sponsorship": "p_reject",
               "use_llm": "p_llm", "apply_threshold": "p_threshold",
               "extra_requirements": "p_extra", "check_h1b_history": "p_h1b"}


def set_widgets(profile: dict) -> None:
    for field, key in WIDGET_KEYS.items():
        ss[key] = profile[field]


if "profile_loaded" not in ss:
    set_widgets(load_profile() if LOCAL else dict(DEFAULT_PROFILE))
    ss.profile_loaded = True


def on_resume_upload():
    f = ss.get("resume_file")
    if f is not None:
        ss.p_resume = extract_resume_text(f.name, f.getvalue())


def on_config_import():
    f = ss.get("config_file")
    if f is None:
        return
    try:
        set_widgets(profile_from_json(f.getvalue()))
        ss.import_msg = "✅ 配置已导入"
    except ValueError as e:  # JSONDecodeError is a ValueError
        ss.import_msg = f"❌ 文件格式不对: {e}"


def owner_key() -> str:
    """The deployer's own key — only honoured in local mode so a shared deployment
    never bills the owner for other people's usage."""
    if not LOCAL:
        return ""
    try:
        return st.secrets.get("ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY", "")


# ------------------------------------------------------------------ sidebar: API
with st.sidebar:
    st.header("⚙️ Claude API")
    api_key = owner_key()
    if api_key:
        st.caption("✅ 本地模式：使用 secrets / 环境变量里的 key")
    else:
        api_key = st.text_input(
            "你的 Anthropic API key", type="password",
            help="只保存在你这次打开的页面里，不会写入服务器。不填则退回关键词匹配。")
        st.caption("用你自己的 key，费用记在你自己的账户。"
                   "[获取 key](https://console.anthropic.com/settings/keys)")
    model = st.selectbox("Model", ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
                         help="Opus 最准 (~$0.05–0.10/次)；Haiku 最便宜 (~$0.002/次)")
    if not LOCAL:
        st.info("🔒 你的简历和设置只存在当前浏览器页面里，其他人看不到；"
                "刷新或关闭页面后会清空 —— 用「导出配置」保存一份到电脑。")

main, side = st.columns([2.2, 1], gap="large")

# ------------------------------------------------------------------ right column: resume + requirements
with side:
    tab_resume, tab_req, tab_cfg = st.tabs(["📄 简历", "✅ 必备条件", "💾 配置"])

    with tab_resume:
        st.file_uploader("上传简历 (PDF / DOCX / TXT)", type=["pdf", "docx", "txt", "md"],
                         key="resume_file", on_change=on_resume_upload)
        resume = st.text_area("或直接粘贴简历文本", height=320, key="p_resume")
        st.caption(f"{len(resume.split())} words" if resume else "⚠️ 还没有简历")

    with tab_req:
        visa = st.text_input("签证身份", key="p_visa")
        c1, c2 = st.columns(2)
        grad_year = c1.number_input("毕业年份", 2024, 2032, key="p_grad_year")
        grad_month = c2.selectbox("毕业月份", range(1, 13), key="p_grad_month",
                                  format_func=lambda m: calendar.month_abbr[m],
                                  help="'2027 Winter' 默认按 Dec 2027；若指 2027 年初请改成 Jan–Mar")
        reject_no_sp = st.toggle("JD 写明不 sponsor → 直接放弃", key="p_reject")
        check_h1b = st.toggle("JD 没提签证时，查公司的 H-1B 记录", key="p_h1b",
                              help="自动查 h1bdata.info（美国劳工部公开数据）；常年办 H-1B 的大公司直接认定。"
                                   "有记录 = 可以 sponsor。MyVisaJobs / H1BGrader 会给出核实链接。")
        use_llm = st.toggle("用 Claude 深度分析技能 (需 API key)", key="p_llm")
        threshold = st.slider("投递阈值 (fit score)", 30, 90, step=5, key="p_threshold")
        extra = st.text_area("其他必备条件 (每行一条)", key="p_extra", height=110,
                             placeholder="e.g.\n必须是 internship / co-op\n地点在 NY / Remote\n不接受 PhD only")

    profile = {"resume": resume, "visa_status": visa, "grad_year": int(grad_year),
               "grad_month": int(grad_month), "reject_no_sponsorship": reject_no_sp,
               "use_llm": use_llm, "apply_threshold": threshold, "extra_requirements": extra,
               "check_h1b_history": check_h1b}
    if LOCAL and profile != ss.get("_saved_profile"):
        save_profile(profile)
        ss._saved_profile = profile

    with tab_cfg:
        st.markdown("把简历 + 设置存成一个文件，下次打开网页时导入即可。")
        st.download_button("⬇️ 导出配置 (jobfit-profile.json)", profile_to_json(profile),
                           file_name="jobfit-profile.json", mime="application/json",
                           width="stretch")
        st.file_uploader("⬆️ 导入配置", type=["json"], key="config_file", on_change=on_config_import)
        if ss.get("import_msg"):
            st.caption(ss.import_msg)
        if LOCAL:
            st.caption("本地模式：配置也会自动保存到 data/profile.json")

# ------------------------------------------------------------------ main column: search + results
ICON = {PASS: "✅", WARN: "⚠️", FAIL: "❌", UNCLEAR: "❔"}
COLOR = {APPLY: "#34A853", MAYBE: "#F9A825", SKIP: "#EA4335"}
LABEL = {APPLY: "✅ 投！APPLY", MAYBE: "🤔 可以考虑 MAYBE", SKIP: "⛔ 不投 SKIP"}


def analyze(jd_text: str, url: str, title: str = "", company: str = ""):
    """Screen one JD and record it in history. Returns the Verdict (or raises)."""
    v = screen(jd_text, profile, api_key=api_key if profile["use_llm"] else "", model=model, company=company,
               title=title)
    v.title, v.company = v.title or title, v.company or company
    row = history_row(verdict=v.decision, score=v.score if v.score is not None else "",
                      company=v.company, title=v.title, url=url, reason=v.reason)
    ss.history.insert(0, row)
    if LOCAL:
        append_history(row)
    return v


def render_result(v, jd_text: str, show_head: bool = True):
    if show_head:
        score = f" · Fit {v.score}/100" if v.score is not None else ""
        head = " — ".join(x for x in (v.company, v.title) if x)
        st.markdown(f'<div class="jf-verdict" style="background:{COLOR[v.decision]}"><h2>{LABEL[v.decision]}{score}</h2>'
                    f'<div>{head}</div><div>{v.reason}</div></div>', unsafe_allow_html=True)
    if v.difficulty is not None:
        st.caption(f"难度 {v.difficulty}/100 — {v.difficulty_reason}")

    for s in v.steps:
        with st.expander(f"{ICON[s.status]}  {s.step} — {s.summary}", expanded=s.status in (FAIL, WARN)):
            for e in s.evidence or ["(no direct quote)"]:
                st.markdown(f"> {e}")
    if v.sponsor is not None:
        links = " · ".join(f"[{name}]({href})" for name, href in v.sponsor.links.items())
        st.caption(f"🔎 核实 {v.sponsor.company} 的 H-1B 记录：{links}")

    rep = v.report
    if rep is not None:
        st.markdown("**技能拆解**")
        cat = {"core_ds": "DS 核心", "learnable_domain": "可速成 domain",
               "sde_out_of_scope": "SDE 超纲", "other": "其他"}
        st.dataframe(
            [{"技能": k.skill, "类别": cat[k.category], "必需": "✔" if k.required else "",
              "你有": "✅" if k.have else "❌", "说明": k.note} for k in rep.skills],
            width="stretch", hide_index=True)
        st.markdown(f"**总结：** {rep.summary}")
        if rep.resume_tips:
            st.markdown("**简历可以这样改：**\n" + "\n".join(f"- {t}" for t in rep.resume_tips))

    with st.expander("📃 读取到的 JD 原文"):
        st.text(jd_text[:20000])


def url_key(url: str) -> str:
    return "mjd_" + hashlib.md5(url.encode()).hexdigest()[:10]


with main:
    mode = st.radio("模式", ["单个岗位", "同公司多岗位对比"], horizontal=True, label_visibility="collapsed")

    # ============================== single job
    if mode == "单个岗位":
        url = st.text_input("JD URL", placeholder="🔍  粘贴职位链接 (Greenhouse / Lever / Workday / LinkedIn / 官网…)",
                            label_visibility="collapsed", key="url")
        b1, b2, b3 = st.columns([1, 1, 3])
        company_in = b3.text_input("公司名", key="company", label_visibility="collapsed",
                                   placeholder="公司名（可选，用于查 H-1B 记录；留空自动识别）")
        go = b1.button("🎯 分析适配度", type="primary", width="stretch")

        def _clear():
            ss.url, ss.manual_jd = "", ""
            ss.fetch_failed, ss.result = False, None
        b2.button("清空", width="stretch", on_click=_clear)

        if ss.fetch_failed:
            st.warning(f"链接打不开或读不到 JD，请手动粘贴 👇\n\n`{ss.fetch_error[:300]}`")
        with st.expander("✍️ 手动粘贴 JD", expanded=ss.fetch_failed):
            manual = st.text_area("JD text", height=260, key="manual_jd", label_visibility="collapsed",
                                  placeholder="把整个 JD 复制进来（包括 Qualifications / Benefits / 签证说明部分）")

        if go:
            jd_text, title, company = "", "", ""
            if manual.strip():
                jd_text = manual.strip()
            elif url.strip():
                with st.spinner("抓取 JD 中…"):
                    try:
                        posting = fetch_job(url)
                        jd_text, title, company = posting.text, posting.title, posting.company
                        ss.fetch_failed = False
                    except FetchError as e:
                        ss.fetch_failed, ss.fetch_error, ss.result = True, str(e), None
                        st.rerun()
            else:
                st.info("先粘贴一个链接或 JD 文本")

            if jd_text:
                if not profile["resume"].strip():
                    st.warning("还没有上传简历 — 只会执行公民身份 / 签证 / 毕业时间三步筛选。")
                with st.spinner("筛选中… (Step 1 命中会直接停止)"):
                    try:
                        ss.result = (analyze(jd_text, url, title, company_in.strip() or company), jd_text)
                    except Exception as e:  # surface API errors without killing the page
                        st.error(f"分析失败: {e}")

        if ss.result:
            render_result(*ss.result)

    # ============================== several jobs at one company
    else:
        ss.setdefault("multi", None)          # list of (url, Verdict, jd_text) from the last run
        ss.setdefault("multi_failed", {})     # url -> fetch error
        st.markdown("同一家公司有多个岗位、但只允许投 1–3 个时用：每行粘一个链接，按**适配度**和**难度**排序，选出最值得投的。")
        urls_raw = st.text_area("JD 链接（每行一个）", key="multi_urls", height=140,
                                placeholder="https://job-boards.greenhouse.io/acme/jobs/123\nhttps://job-boards.greenhouse.io/acme/jobs/456")
        c1, c2, c3 = st.columns([1.2, 1.2, 2])
        max_apply = c1.number_input("最多可投几个", 1, 10, 2, key="multi_max")
        multi_company = c3.text_input("公司名（可选，用于查 H-1B 记录）", key="multi_company",
                                      placeholder="留空则从页面自动识别")
        go_multi = c2.button("🎯 对比这些岗位", type="primary", width="stretch")

        urls = list(dict.fromkeys(u.strip() for u in urls_raw.splitlines() if u.strip()))
        failed = [u for u in urls if u in ss.multi_failed]
        if failed:
            st.warning(f"{len(failed)} 个链接读不到 JD，请把 JD 粘贴到下面对应的框里，再点一次对比 👇")
            for u in failed:
                with st.expander(f"✍️ {u}", expanded=not ss.get(url_key(u))):
                    st.caption(ss.multi_failed[u][:200])
                    st.text_area("JD text", key=url_key(u), height=180, label_visibility="collapsed")

        if go_multi:
            if len(urls) < 2:
                st.info("至少粘贴 2 个链接；只有一个岗位请用「单个岗位」模式。")
            else:
                jds, fails = {}, {}

                def fetch_one(u):
                    pasted = (ss.get(url_key(u)) or "").strip()
                    if pasted:
                        return u, (pasted, "", ""), None
                    try:
                        p = fetch_job(u)
                        return u, (p.text, p.title, p.company), None
                    except FetchError as e:
                        return u, None, str(e)

                with st.spinner(f"抓取 {len(urls)} 个 JD…"):
                    with ThreadPoolExecutor(max_workers=6) as pool:
                        for u, got, err in pool.map(fetch_one, urls):
                            if got:
                                jds[u] = got
                            else:
                                fails[u] = err
                ss.multi_failed = fails

                results = []
                with st.spinner(f"分析 {len(jds)} 个岗位…"):
                    # same company for every job: look it up once before the parallel run
                    if profile["check_h1b_history"] and multi_company.strip():
                        check_sponsor(multi_company.strip())
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        futures = {u: pool.submit(screen, jd, profile,
                                                  api_key=api_key if profile["use_llm"] else "", model=model,
                                                  company=multi_company.strip() or co, title=t)
                                   for u, (jd, t, co) in jds.items()}
                    for u, fut in futures.items():
                        jd, title, company = jds[u]
                        try:
                            v = fut.result()
                        except Exception as e:
                            st.error(f"{u} 分析失败: {e}")
                            continue
                        v.title, v.company = v.title or title, v.company or company
                        row = history_row(verdict=v.decision, score=v.score if v.score is not None else "",
                                          company=v.company, title=v.title, url=u, reason=v.reason)
                        ss.history.insert(0, row)
                        if LOCAL:
                            append_history(row)
                        results.append((u, v, jd))
                ss.multi = results
                if fails:
                    st.rerun()  # show paste boxes for the links that couldn't be read

        if ss.multi:
            by_v = {id(v): (u, jd) for u, v, jd in ss.multi}
            ranked = rank_jobs([v for _, v, _ in ss.multi], int(max_apply))
            picks = [v for v, rec in ranked if rec]
            if picks:
                names = "、".join(v.title or by_v[id(v)][0] for v in picks)
                st.success(f"**建议投这 {len(picks)} 个：** {names}")
            else:
                st.error("这些岗位都没通过硬性筛选（公民身份 / 签证 / 毕业时间 / SDE 超纲），建议都不投。")
            st.dataframe([{
                "排名": i, "推荐": "⭐" if rec else "", "职位": v.title or "—", "结论": v.decision,
                "适配度": v.score,
                "难度": v.difficulty,
                "优先级": round(priority(v)) if v.decision != SKIP else None,
                "原因": v.reason, "链接": by_v[id(v)][0],
            } for i, (v, rec) in enumerate(ranked, 1)], width="stretch", hide_index=True,
                column_config={"链接": st.column_config.LinkColumn("链接", display_text="打开")})
            st.caption(f"优先级 = 适配度 − {DIFFICULTY_WEIGHT} × 难度。没通过硬性筛选的岗位排在最后，不会被推荐。")
            for i, (v, rec) in enumerate(ranked, 1):
                u, jd = by_v[id(v)]
                with st.expander(f"{'⭐ ' if rec else ''}#{i} {LABEL[v.decision]} · {v.title or u}"):
                    st.caption(v.reason)
                    render_result(v, jd, show_head=False)

    # ---------------- history (this visitor only)
    if ss.history:
        with st.expander(f"🗂 历史记录 ({len(ss.history)})"):
            st.dataframe(ss.history, width="stretch", hide_index=True)
            st.download_button("⬇️ 下载历史 CSV", history_to_csv(ss.history),
                               file_name="jobfit-history.csv", mime="text/csv")
