# 🎯 JobFit — 这个岗位投不投？

粘贴 JD，对照你的简历给出 **APPLY / MAYBE / SKIP**。为找 Data Science 岗位的 F-1 国际学生设计。

## 直接用

**网页版（点开即用）：https://claude.ai/artifact/EU24GEsT3LmGMTeQ9E1FJp**

- 登录 Claude 账号后打开即可，不用安装，也不用 API key；分析用的是你自己的 Claude 额度。
- 只能粘贴 JD 文本（网页不能替你打开别的网站）。简历和设置只存在你自己的浏览器里。

## 自己部署 Streamlit 版（能自动读取 JD 链接）

1. Fork 或 clone 本仓库。
2. 打开 [share.streamlit.io](https://share.streamlit.io)，用 GitHub 登录 → **Create app**。
3. 仓库选 `resume-match`，分支 `main`，主文件 `app.py` → Deploy。
4. **不要**在 Secrets 里填 API key：每个访问者在侧边栏填自己的 [Anthropic API key](https://console.anthropic.com/settings/keys)，费用各算各的。

本地运行（会自动保存简历，并读取你自己的 key）：

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 填入 ANTHROPIC_API_KEY
JOBFIT_LOCAL=1 streamlit run app.py
```

## 筛选规则（按顺序，前一步不过就停）

| 步骤 | 检查 | 不通过 |
|---|---|---|
| 1 | 要求美国公民 / 绿卡 / 永久工作许可 / 安全许可 / ITAR | 直接放弃，后面不再检查 |
| 2 | 是否接受 OPT / CPT；提供 H-1B 最好。JD 没提签证时，查公司有没有 H-1B 申请记录（见下） | 写明不 sponsor → 放弃（可关闭） |
| 3 | 毕业时间（默认 2027 年 12 月）是否在要求范围内 | 放弃 |
| 4 | **技术门槛**：看岗位日常用的工具和技术深度，不看行业和职位名称（见下） | 技术栈以 SDE 为主、深度远超简历或匹配度低 → 放弃 |

## 技术门槛怎么判断

- **行业不扣分。** 实习 / new grad 的行业知识本来就浅，入职一两周就能补上，所以供应链、物流、金融、营销、风控、医疗都不影响评分。
- **看技术栈和深度。** 例如 Supply Chain Analyst 每天用 Python、SQL、做 dashboard，技术门槛就和 Data Analyst 一样，可以投。
- **不看职位名称。** 叫 Data Scientist、实际是写 Java 后端服务的岗位，算 SDE，不投。
- Claude 会列出岗位的技术栈，并判断技术门槛是「一致」「略高」（需要更深的 ML / Spark 等，或缺一两个能快速学会的工具）还是「超出」（研究级建模、重工程），「超出」直接放弃。
- 没有 Claude 时用关键词兜底：按数据工具的重合度打分；SDE 技能多于数据工具时判定为 SDE 岗位。

## 公司 H-1B 记录

JD 没写签证政策时，公司办过 H-1B 就说明它能 sponsor：

- **常年大量办 H-1B 的大公司**（Amazon、Google、JPMorgan、Deloitte 等）直接认定。
- **Streamlit 版**会自动查 [h1bdata.info](https://h1bdata.info)（美国劳工部公开数据），显示近一年的申请条数，以及其中 Data/Analytics 相关的条数。
- **网页版**不能访问外部网站，改由 Claude 根据自己的知识判断，标注"请核实"。
- 两个版本都会给出 [MyVisaJobs](https://www.myvisajobs.com)、E-Verify（STEM OPT 需要）、[H1BGrader](https://h1bgrader.com) 的核实链接。这两个网站有反爬虫验证，没法自动查询。
- JD 明确写了"不 sponsor"时以 JD 为准，公司历史记录不改变结论。
- 公司名默认自动识别，识别不准时可以手动填写。

## 同公司多岗位对比

有的公司只允许投 1–3 个岗位。切到「同公司多岗位对比」，一次放入多个岗位（Streamlit 版每行一个链接，网页版每个框粘一个 JD），再设好最多可投几个：

- 每个岗位都按上面 4 步筛选，另外给出**难度**（0–100，参考经验年限、学历、职级、团队竞争程度）。
- 按 **优先级 = 适配度 − 0.4 × 难度** 排序，标出最该投的前 N 个；没通过硬性筛选的岗位排在最后，不推荐。

## 两个版本对比

| | 网页版 | Streamlit 版 |
|---|---|---|
| 输入 JD 链接 | ❌ 需手动粘贴 | ✅ Greenhouse / Lever / Ashby / Workday / LinkedIn / 官网 |
| 上传简历 | 粘贴文本或 .txt | PDF / DOCX / TXT |
| AI 费用 | 访问者自己的 Claude 账号 | 访问者自己的 API key |
| 数据存储 | 访问者的浏览器 | 访问者的页面会话（本地模式存到 `data/`） |

## 其他

- **Claude Code 技能：** 把 `skill/job-fit/` 复制到 `~/.claude/skills/`，在 Claude Code 里发链接即可分析。
- **代码结构：**
  - `app.py`：Streamlit 界面
  - `jobfit/`：抓取 JD、筛选规则、Claude 评分
  - `web/jobfit.html`：网页版
  - `tests/`：规则测试（运行 `pytest`）
- **调整规则：** 改 `jobfit/rules.py` 里的正则；网页版的同一套规则在 `web/jobfit.html`，两边要一起改。
