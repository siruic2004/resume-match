"""JobFit — paste a JD URL, get an apply / skip verdict against your resume.

Run:  streamlit run app.py                  (shared mode: per-visitor data, bring-your-own key)
      JOBFIT_LOCAL=1 streamlit run app.py   (local mode: saves to data/, reads ANTHROPIC_API_KEY)
"""
import calendar
import os

import streamlit as st

from jobfit.fetcher import FetchError, fetch_job
from jobfit.pipeline import APPLY, MAYBE, SKIP, screen
from jobfit.profile import (DEFAULT_PROFILE, append_history, extract_resume_text, history_row,
                            history_to_csv, is_local_mode, load_history, load_profile,
                            profile_from_json, profile_to_json, save_profile)
from jobfit.rules import FAIL, PASS, UNCLEAR, WARN

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
               "extra_requirements": "p_extra"}


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
        use_llm = st.toggle("用 Claude 深度分析技能 (需 API key)", key="p_llm")
        threshold = st.slider("投递阈值 (fit score)", 30, 90, step=5, key="p_threshold")
        extra = st.text_area("其他必备条件 (每行一条)", key="p_extra", height=110,
                             placeholder="e.g.\n必须是 internship / co-op\n地点在 NY / Remote\n不接受 PhD only")

    profile = {"resume": resume, "visa_status": visa, "grad_year": int(grad_year),
               "grad_month": int(grad_month), "reject_no_sponsorship": reject_no_sp,
               "use_llm": use_llm, "apply_threshold": threshold, "extra_requirements": extra}
    if LOCAL and profile != ss.get("_saved_profile"):
        save_profile(profile)
        ss._saved_profile = profile

    with tab_cfg:
        st.markdown("把简历 + 设置存成一个文件，下次打开网页时导入即可。")
        st.download_button("⬇️ 导出配置 (jobfit-profile.json)", profile_to_json(profile),
                           file_name="jobfit-profile.json", mime="application/json",
                           use_container_width=True)
        st.file_uploader("⬆️ 导入配置", type=["json"], key="config_file", on_change=on_config_import)
        if ss.get("import_msg"):
            st.caption(ss.import_msg)
        if LOCAL:
            st.caption("本地模式：配置也会自动保存到 data/profile.json")

# ------------------------------------------------------------------ main column: search + results
with main:
    url = st.text_input("JD URL", placeholder="🔍  粘贴职位链接 (Greenhouse / Lever / Workday / LinkedIn / 官网…)",
                        label_visibility="collapsed", key="url")
    b1, b2, _ = st.columns([1, 1, 3])
    go = b1.button("🎯 分析适配度", type="primary", use_container_width=True)

    def _clear():
        ss.url, ss.manual_jd = "", ""
        ss.fetch_failed, ss.result = False, None
    b2.button("清空", use_container_width=True, on_click=_clear)

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
                    v = screen(jd_text, profile, api_key=api_key if profile["use_llm"] else "", model=model)
                except Exception as e:  # surface API errors without killing the page
                    st.error(f"分析失败: {e}")
                    v = None
            if v:
                v.title, v.company = v.title or title, v.company or company
                ss.result = (v, jd_text)
                row = history_row(verdict=v.decision, score=v.score if v.score is not None else "",
                                  company=v.company, title=v.title, url=url, reason=v.reason)
                ss.history.insert(0, row)
                if LOCAL:
                    append_history(row)

    # ---------------- render result
    if ss.result:
        v, jd_text = ss.result
        color = {APPLY: "#34A853", MAYBE: "#F9A825", SKIP: "#EA4335"}[v.decision]
        label = {APPLY: "✅ 投！APPLY", MAYBE: "🤔 可以考虑 MAYBE", SKIP: "⛔ 不投 SKIP"}[v.decision]
        score = f" · Fit {v.score}/100" if v.score is not None else ""
        head = " — ".join(x for x in (v.company, v.title) if x)
        st.markdown(f'<div class="jf-verdict" style="background:{color}"><h2>{label}{score}</h2>'
                    f'<div>{head}</div><div>{v.reason}</div></div>', unsafe_allow_html=True)

        icon = {PASS: "✅", WARN: "⚠️", FAIL: "❌", UNCLEAR: "❔"}
        for s in v.steps:
            with st.expander(f"{icon[s.status]}  {s.step} — {s.summary}", expanded=s.status in (FAIL, WARN)):
                for e in s.evidence or ["(no direct quote)"]:
                    st.markdown(f"> {e}")

        rep = v.report
        if rep is not None:
            st.subheader("技能拆解")
            cat = {"core_ds": "DS 核心", "learnable_domain": "可速成 domain",
                   "sde_out_of_scope": "SDE 超纲", "other": "其他"}
            st.dataframe(
                [{"技能": k.skill, "类别": cat[k.category], "必需": "✔" if k.required else "",
                  "你有": "✅" if k.have else "❌", "说明": k.note} for k in rep.skills],
                use_container_width=True, hide_index=True)
            st.markdown(f"**总结：** {rep.summary}")
            if rep.resume_tips:
                st.markdown("**简历可以这样改：**\n" + "\n".join(f"- {t}" for t in rep.resume_tips))

        with st.expander("📃 读取到的 JD 原文"):
            st.text(jd_text[:20000])

    # ---------------- history (this visitor only)
    if ss.history:
        with st.expander(f"🗂 历史记录 ({len(ss.history)})"):
            st.dataframe(ss.history, use_container_width=True, hide_index=True)
            st.download_button("⬇️ 下载历史 CSV", history_to_csv(ss.history),
                               file_name="jobfit-history.csv", mime="text/csv")
