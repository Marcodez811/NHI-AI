# 立院QA skill: output analysis and open questions

**Date:** 2026-10-06. **Status:** waiting on PM answers (section 4) before planning the skill.
**Builds on:** `docs/9_26_chat_harness_options.md` (skills design), the files and artifacts work
(commit `cc1406fa`, `report` artifacts).

**Sources:**
- The real output sample is 《11-4會期立法院模擬題例行性撰擬》 (`.docx`, 15 topics).
- The PM's flow chart for 立院QA/活動擬答 is in 「專案優化建議整理20260922＿機器人.docx」.

We reverse-engineered the inputs from the output, so we can ask the PM precise questions.

---

## 1. What the output looks like

The document is a routine batch prepared for each 會期 (here 11-4), not a one-off. Each of the
15 topics has two parts: a short version first, then the title repeated with a detailed version.

| | Short version (簡答) | Detailed version (詳答) |
| --- | --- | --- |
| Font | **26pt**, bold headings | 16pt, bold headings |
| Purpose | Read aloud at the podium (現場快速答詢) | Background and answers to follow-up questions (背景掌握／延伸追問) |
| Structure | 2–5 points as 一、(一)… | Mostly **一、背景說明 二、爭議點 三、目前辦理情形 四、未來工作重點** (topics 6–12). Topics 1–5 use free headings, e.g. 計畫目的／實施方式／執行成果 |
| Content (flow chart) | Direct answer, core measures, key figures, results | Background, policy aim, points of dispute, current status, detailed figures, future focus |

Format details:

- A4 landscape, Times New Roman, no tables, everything in the "Normal" style.
- Numbering is typed by hand: `N.` for topics, then 一、 (一) 1.
- It's written in the agency's own voice (本署) and uses ROC years (114年, 113.5.1).
- **Dense with figures:**
  - 點值 to four decimals, e.g. 「臺北區 0.9349（去年0.8976）」;
  - budgets in 億元 by year;
  - patient and institution counts;
  - comparisons with last year.
- **No source citations** anywhere in the document.

### Example (topic 6, shortened)

```
6. 強化偏鄉地區醫療服務及離島加成                          ← 26pt, short version
一、政策與計畫：
(一) 本署透過「山地離島地區醫療給付效益提昇計畫（IDS）」…
(三) …給予浮動點值「最高 1 點 1 元」保障。
二、離島與偏鄉之支付加成與就醫負擔減免：
(一) 住診與急診：離島地區之住診案件…加計 30%（113.5.1 起生效）…

強化偏鄉地區醫療服務及離島加成                            ← 16pt, detailed version
一、背景說明：本署因應山地離島與偏鄉醫療資源不足，自88年起推動IDS…
二、爭議點：偏鄉就醫可近性已改善，惟仍存有健康不平等議題…
三、目前辦理情形：
(一) IDS與全人方案：112年6.054億元、113年8.554億元…
(七) 燈塔型地區醫院補助：113年起以2億元為上限…符合名單72家，符合補助條件33家。
四、未來工作重點：
(一) 依計畫持續推動與滾動檢討…
```

---

## 2. The PM's flow chart

```
情境觸發（收到長官可能被質詢之問題）
→ 需求解析（判斷質詢核心／需要回答什麼）
→ 資料輸入（健保署參考資料＋既有政策／業務資料）
→ 資訊整理（數據、政策目的、執行情形、爭議、後續作法）
→ 答題架構確認（確認回答主軸與資訊層次）
→ 簡答版產製 ∥ 詳答版產製
→ 一致性檢核（數據／政策內容／說法一致）
→ 人工確認修正
→ 完成 QA 文件
```

The output matches the chart: the short and detailed versions follow its bullet lists, and the
consistency check matters because the same figures appear in both versions.

---

## 3. Inputs, reverse-engineered

1. **The topic list.** Someone hands over the topics the minister may be asked about, usually
   as short titles (「強化偏鄉地區醫療服務及離島加成」).
2. **Facts and figures per topic: the critical input.** Numbers like 「113年13億元」 or
   「符合名單72家」 cannot be generated. The model can only arrange figures that exist in the
   knowledge base or in uploaded documents, so **whether these documents exist decides whether the
   skill works.**
3. **Occasion and time frame:** the 會期 and the reporting period (e.g. 114年第1季), which decide
   which figures are "current".
4. **Optional:**
   - the legislator's angle or a news item (爭議點 needs to know what is controversial);
   - last session's QA on the same topic (a strong starting point for updates).

---

## 4. Questions for the PM

| # | Question | Why it matters |
| --- | --- | --- |
| 1 | **Where do the figures come from?** 備參, annual reports, internal statistics? Are they, or could they be, in our knowledge base? | Decides whether the skill is feasible at all |
| 2 | What arrives at the start: only topic titles, or the legislator's actual questions? One topic at a time, or a batch like this one? | Decides what 需求解析 asks and whether we need batch runs |
| 3 | Is last session's QA reused and updated? | "Update this QA with new figures" may be the main use case |
| 4 | Is this layout the official template (26pt/16pt, A4 landscape)? Should every detailed version use 背景說明／爭議點／目前辦理情形／未來工作重點, or stay free like topics 1–5? | Decides the Word export and the detailed-version structure |
| 5 | The final document has no sources. Should the app show sources for checking but leave them out of the Word file? | We recommend yes: every figure stays traceable and the document stays clean |
| 6 | Who does 人工確認修正: the drafter or a supervisor? | Decides the review step and any sign-off |

**For the PM (中文版，可直接轉貼):**

1. 模擬題裡的數據（點值、預算、人數等）通常從哪裡來？（備參、年報、內部統計…）這些資料目前或之後能放進知識庫嗎？
2. 承辦一開始拿到的是什麼？只有題目標題，還是委員的實際提問內容？通常一次一題，還是像這份一樣一次整批？
3. 每個會期是否會沿用上一會期的 QA 再更新數據？
4. 這份的格式（簡答 26 號字、詳答 16 號字、A4 橫式）是固定範本嗎？詳答是否都要用「背景說明／爭議點／目前辦理情形／未來工作重點」四段？
5. 最終文件沒有列資料來源。系統畫面上顯示來源供核對、但匯出的 Word 不放來源，這樣可以嗎？
6. 「人工確認修正」由誰負責？承辦本人，還是需要長官審閱？

---

## 5. Implications for the plan (if question 1 is "the figures are in our documents")

The flow from the planning session still fits, with two additions:

- **Batch support:** several topics per run, each going through outline → short ∥ detailed →
  consistency check.
- **Word export that copies this layout:** A4 landscape, 26pt short version, title repeated, 16pt
  detailed version. It is saved as a `report` artifact.

Planned shape (to be detailed after the PM answers):

| Step | Who | How |
| --- | --- | --- |
| 情境觸發 | User | 「＋ → 使用技能 → 立院QA」, or the agent suggests it |
| 需求解析 | Agent | Asks only for what is missing: topics, 會期, reporting period |
| 資料輸入 | User and agent | Uploads, attached knowledge-base documents, knowledge-base search |
| 資訊整理 | Agent | Key facts and figures per topic, **with sources** |
| 答題架構確認 | **User checkpoint** | Outline card per topic: 確認 / 修改 |
| 簡答 ∥ 詳答 | Backend tool | Two model calls in parallel with fixed inputs |
| 一致性檢核 | Backend tool | A third call compares figures, policy content and wording, and flags conflicts |
| 人工確認修正 | **User checkpoint** | Side-panel preview; fixes are requested in chat |
| 完成 QA 文件 | Backend | Word file in the sample's layout, saved as a `report` artifact |

The generation steps are backend tools rather than free chat writing. This makes them more
reliable, lets the two versions really run in parallel, and makes the consistency check a separate
check instead of the model approving its own work.
