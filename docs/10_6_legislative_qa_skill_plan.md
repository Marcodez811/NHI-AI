# Plan: the 立院QA skill

**Date:** 2026-10-06.
**Builds on:**
- `docs/10_6_legislative_qa_output_analysis.md` (output format; PM answers below);
- `docs/9_26_chat_harness_options.md` (skills);
- the files and artifacts work (`cc1406fa`);
- `config.yaml` (`docs/10_6_config_yaml_and_settings_spec.md`).

**Hard rules:**
- Traditional Chinese UI; no internal ids shown; API keys stay on the backend.
- Every figure in the output must come from a document the user supplied or picked.

## PM answers (2026-10-06 meeting)

| Topic | Answer |
| --- | --- |
| Trigger | The user switches the chat into 立院QA mode and pastes the questions 長官 may be asked (possibly many) |
| 需求解析 | Back-and-forth Q&A until the list of questions to answer is confirmed |
| 資料輸入 | The AI first lists documents that may help; the user then uploads and filters documents |
| 資訊整理 | From the inputs, list per question: figures; policy aim, status and dispute; next steps |
| 答題架構確認 | Back-and-forth on structure and outline; the user confirms, then generation starts |
| Template | Fixed: 簡答 26pt, 詳答 16pt, A4 landscape, as in the sample |
| 詳答 sections | Confirmed in 答題架構. 爭議點 only when the user brings it up |
| Sources | **Unlike the sample,** every paragraph cites `[1]`, `[2]`…, with a reference list at the end |
| Edits | After generation, the user goes back to the same chat to revise |

Product decisions (2026-10-06):
- slim stepper at the top of the chat;
- all questions generated in one run, in parallel;
- side-panel preview;
- **writer/checker** generation.

Font, from the sample's XML: Times New Roman for Latin text and digits, **標楷體** for Chinese
(the sample uses `KaiTi`; we use 標楷體 / DFKai-SB, the Traditional Chinese government standard).

---

## Architecture

```
Chat (立院QA mode) ──tools──▶ QA 工作區 (per conversation, in the DB)
   需求解析 / 資料輸入 / 資訊整理 / 答題架構      │
                                                  ▼ confirmed
                        legislative_qa workflow job (existing agentic job system)
                        writer (author) ⇄ checker (reviewer) + numeric check
                                                  │
                                                  ▼
                        .docx (fixed template) → report artifact (versioned)
                        → side-panel preview + 下載 Word
```

### 1. Skill mode on a conversation

- `chat_sessions.skill` (nullable, `legislative_qa`). It is set from 「＋ → 使用技能 → 立院QA」,
  which enables the currently disabled menu item. It is sticky: later edits continue in the same
  mode.
- In this mode the engine adds the skill's instructions (`backend/chat_skills/legislative_qa/SKILL.md`)
  and the skill tools below. Normal chat tools (knowledge-base search, read attachment) stay
  available.
- The composer shows a 「立院QA」 chip (×: leave the mode, with confirmation).

### 2. QA 工作區 (structured state)

Long flows get compacted, so confirmed decisions live as data, not only as chat text. One row per
conversation (JSON columns are fine):

| Part | Content |
| --- | --- |
| `questions` | `[{no, text, note}]`, confirmed in 需求解析 |
| `documents` | Attached knowledge-base documents and uploads, reusing `chat_session_documents` / `chat_session_files` |
| `evidence` | Per question: `{figures[], aim, status, dispute, next_steps}`, each item with `source` (document) and `quote` (the supporting text) |
| `outline` | Per question: 簡答 points, 詳答 sections (default 背景說明／目前辦理情形／未來工作重點; 爭議點 only if requested), confirmed flag |
| `stage` | `questions → documents → evidence → outline → generating → review` |
| `versions` | Generated artifact ids, newest first |

The workspace is shown to the model in the turn context (as with attachments), so it never
depends on old chat turns.

### 3. Skill tools (the AI calls these; each returns data the UI renders as a card)

| Tool | Stage | Card |
| --- | --- | --- |
| `set_questions(questions)` | 需求解析 | 題目清單: 確認 / 修改 |
| `suggest_documents(per_question)` | 資料輸入 | Knowledge-base documents per question with checkboxes + 上傳; ticking attaches the document to the conversation |
| `record_evidence(question_no, items)` | 資訊整理 | Per question, a collapsible list of figures, aim, status, dispute and next steps, each with its source |
| `propose_outline(question_no, outline)` | 答題架構 | 大綱 per question: 確認 / 修改; 「全部確認並開始產製」 when all are confirmed |
| `start_generation()` | 報告生成 | Progress card; only allowed when every outline is confirmed |
| `revise(question_no, part, instruction)` | 修改 | Starts a scoped regeneration → new version |

Card actions (確認, ticking documents) post back to the API and update the workspace directly, so
they don't depend on the model obeying text.

### 4. Generation: writer/checker on the existing agentic job system

A new workflow `legislative_qa` in `workflow_registry`, next to slides and news. It reuses jobs,
progress events, telemetry and the per-stage model settings (`agent_stage_settings`, with defaults
in `config.yaml`).

- **Writer (author stage):**
  - For each question, two calls in parallel: 簡答 and 詳答.
  - Inputs: the question, its confirmed outline, and **only** its evidence.
  - Structured output: `sections[{heading, paragraphs[{text, cites[evidence ids]}]}]`.
- **Numeric check (code, no model):** every number in the draft (amounts, 點值, percentages, counts,
  dates) must appear in the quotes of the evidence it cites. Misses become issues.
- **Checker (reviewer stage, separate model):** reads the whole draft plus evidence and returns
  `issues[{question_no, part, kind, message}]`. `kind` is one of:
  - `inconsistent` (簡答 vs 詳答 figures, positions or wording);
  - `unsupported` (claim not in evidence);
  - `off_question`;
  - `outline` (sections differ from the confirmed outline, e.g. an unrequested 爭議點);
  - `style` (numbering, ROC dates, 本署 voice).
- **Loop:**
  - The writer revises only the flagged parts.
  - At most `skills.legislative_qa.max_review_rounds` rounds (`config.yaml`, default 2); stop
    early when no issue is resolved, using the existing stagnation logic.
  - Unresolved issues are kept and shown as ⚠ in the preview for 人工確認修正.
- **Models:**
  - `writer` = author stage, `checker` = reviewer stage, set on the QA workflow's 模型設定 tab
    (listed under AI 工作流 like the others).
  - Defaults in `config.yaml`. The checker should default to a different, strong model.

### 5. The document

- `python-docx`, A4 landscape. The fonts and sizes are fixed by code, not taken from the user's
  Word defaults.
- Per question:
  - `N. 題目` + the 簡答 sections at 26pt (bold headings);
  - then the title repeated + the 詳答 sections at 16pt.
  - Numbering 一、 (一) 1. typed as text, as in the sample.
- Citations `[n]` after the sentence. Numbers are global across the document. The 參考資料 list at
  the end gives the document display name; uploads and knowledge-base documents look the same,
  and no ids are shown.
- Saved as a `report` artifact (「產出的文件」 in 我的檔案), titled `<會期> 立法院QA（N 題）`. Each
  generation or revision adds a new version; older versions stay downloadable.

### 6. UI

- **Stepper** (one line, under the chat header): 題目 → 資料 → 整理 → 架構 → 生成, with the current
  step highlighted. Clicking a finished step scrolls to its card.
- **Cards** as above, in the message list, built from `components/ui`.
- **Side panel** (right; full-screen sheet on mobile):
  - a rendered preview with tabs per question (簡答 / 詳答);
  - ⚠ markers for unresolved checker issues;
  - a version switcher;
  - 下載 Word.

  The preview renders the same structured content the .docx is built from, so the two cannot
  diverge.

---

### 7. Starting from a previous QA (decided 2026-10-06)

Like building on an earlier artifact in Claude: in 情境觸發 the user can pick 「從先前的 QA 開始」.

- **A QA this system generated (lossless).**
  - Every generated version stores its structured content next to the .docx: questions, outlines,
    sections, paragraphs, citations and evidence.
  - The user picks it from 我的檔案 → 產出的文件 (or 「＋ → 從我的檔案加入」), which pre-fills the
    QA 工作區. The user then confirms which questions to keep, drop or add, and attaches the new
    period's documents.
  - Generation runs in **update mode**: the writer gets the previous text as a baseline and keeps
    its wording where the evidence still holds. The preview highlights figures and sentences that
    changed against the previous version.
- **A pre-system Word QA (best effort).**
  - An importer splits the .docx into topics (`N. 題目`). It separates 簡答 from 詳答 by font size
    (26pt / 16pt) or the repeated title, and extracts section headings.
  - Imported figures carry **no source**, so they are marked 「未附來源」. Each must be matched to
    new evidence or explicitly confirmed by the user before generation; otherwise the checker
    reports it as `unsupported`.

## Phases

1. **Skill mode + workspace + the first four stages.** Skill flag and composer chip, workspace
   table, the tools for 題目 / 資料 / 整理 / 架構 with their cards, and the stepper. No generation yet:
   the end state is a confirmed outline with evidence. *Testable by chatting through a real
   question.*
2. **Generation.** The `legislative_qa` workflow: writer, numeric check, checker loop, model
   settings tab, .docx template, report artifact (**with its structured content stored**, see §7),
   progress card, and starting from a previously generated QA (update mode). *Test: generate the
   sample's topic 6 from the sample's own figures and compare with the original.*
3. **Preview + revisions.** Side panel, versions with change highlighting, ⚠ issues, scoped
   `revise` from chat, and the importer for pre-system Word QAs.

## Open questions (not blocking Phase 1)

- Who signs off: the drafter only, or a supervisor review step? Single user for now, so not
  modelled.
- A 會期 label for the document title: ask in 需求解析, or a setting?
