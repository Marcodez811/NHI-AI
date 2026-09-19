# NHI-Style Institutional News Writing Skill

## Purpose

This skill enables an agent to write clear, source-grounded institutional news releases from one or more source documents.

The target writing style follows the recurring structure used in Taiwan National Health Insurance Administration news releases:

1. State the news immediately.
2. Explain why it matters.
3. Describe the policy, program, result, warning, or service change.
4. Support the announcement with concrete facts and numbers.
5. Attribute interpretation or policy intent to the responsible authority.
6. Translate administrative action into practical public benefit.
7. Close with next steps, future direction, or a public call to action.

The output should read like an official public-sector press release, not like a newspaper article, opinion piece, marketing advertisement, or academic report.

---

## When to Use This Skill

Use this skill when the user provides source materials such as:

- policy documents
- official meeting minutes
- program announcements
- reimbursement or payment changes
- benefit coverage decisions
- research or pilot program results
- statistical summaries
- service rollout documents
- public notices
- fraud or scam warnings
- event briefs
- internal briefing documents
- spreadsheets containing implementation figures
- PDFs, Word files, slide decks, or plain text documents

Use this skill when the desired output is an official news release intended for publication on a government, public institution, healthcare organization, or similar institutional website.

Do not use this skill when the user wants:

- an investigative news article
- an opinion column
- a social media post
- a technical report
- meeting minutes
- a policy memo
- an academic abstract
- a direct copy of a source document

---

## Core Principle

Every factual claim in the article must be traceable to the source documents.

Never invent:

- dates
- amounts
- percentages
- beneficiary counts
- policy names
- institution names
- official titles
- quotations
- eligibility conditions
- implementation details
- medical outcomes
- legal interpretations
- contact information
- URLs
- future commitments

If information is missing, write around the gap or flag it for the user. Do not fill missing details with plausible assumptions.

---

# Input Model

The agent may receive one source document or multiple documents.

Before drafting, extract the following information whenever available.

## 1. News Event

Identify the single main event.

Examples:

- a new reimbursement policy begins
- coverage expands
- payment points increase
- a pilot program launches
- a program reports positive results
- a service becomes available
- an international journal publishes program results
- a public warning is issued
- an official event is held

The final article must have one dominant news angle.

If multiple source documents contain several unrelated announcements, do not combine them blindly. Determine whether they belong to one coherent announcement. If not, separate them or ask the user how they should be grouped.

---

## 2. Effective Date or Event Date

Extract:

- announcement date
- effective date
- application period
- implementation date
- event date
- evaluation period

Keep these dates distinct.

Do not convert:

"approved on July 20"

into:

"effective July 20"

unless the source explicitly says so.

---

## 3. Policy or Program Context

Identify:

- the problem being addressed
- existing policy background
- previous limitations
- population need
- healthcare challenge
- government policy objective
- relevant strategic initiative

Use this information to answer:

"Why is this announcement necessary?"

---

## 4. Responsible Authority

Extract:

- agency
- department
- ministry
- hospital
- research institution
- spokesperson
- director-general
- minister
- project lead
- other named officials

Verify official titles from the source.

Do not shorten or upgrade a person's title unless clearly supported.

---

## 5. Target Population

Identify exactly who is affected.

Examples:

- patients with a specific disease
- healthcare institutions
- medical professionals
- insured persons
- dependents
- families
- elderly patients
- children
- hospitals participating in a pilot
- people receiving a defined treatment

Avoid broadening the affected population.

If the policy applies to 5,800 eligible patients, do not write "all patients."

---

## 6. Measures

Extract each concrete measure separately.

For each measure, capture:

- what changes
- previous situation
- new situation
- eligibility requirements
- payment amount
- reimbursement points
- covered treatment
- implementation mechanism
- participating institutions
- procedural change
- system feature
- required user action

Each measure should become one clear explanatory unit in the article.

---

## 7. Quantitative Evidence

Collect all usable figures.

Examples:

- total budget
- annual investment
- number of beneficiaries
- number of participating institutions
- reimbursement points
- percentage increases
- treatment completion rate
- emergency visit rate
- readmission rate
- number of users
- number of applications
- total cases
- estimated annual impact

Preserve units exactly.

Check whether the figure is:

- actual
- estimated
- projected
- annual
- cumulative
- monthly
- per case
- per person

Use the correct qualifier.

For example:

"預估約1萬人受惠"

must not become:

"已有1萬人受惠"

---

## 8. Outcomes and Public Benefits

Extract explicit benefits from the documents.

Common benefit categories include:

- improved access to treatment
- reduced financial burden
- improved continuity of care
- earlier diagnosis
- faster treatment
- reduced caregiver burden
- greater convenience
- improved patient outcomes
- improved quality of care
- improved healthcare capacity
- reduced administrative burden
- improved safety
- improved system efficiency

Do not claim benefits not supported by the source.

---

## 9. Quotable Statements

If the source contains direct statements from officials, record:

- exact speaker
- exact title
- exact wording
- context

Direct quotes may be shortened only if the meaning remains unchanged.

Never fabricate an official quote.

If no usable quote exists, use indirect attribution instead:

"健保署表示，..."

or:

"健保署指出，..."

Only do this when the underlying interpretation is supported by the source.

---

## 10. Public Action or Closing Information

Extract:

- website
- hotline
- application instructions
- warning instructions
- service access steps
- event registration information
- future implementation plans
- follow-up commitments
- links to relevant systems
- attachment references

These details often belong in the final paragraph.

---

# Source Analysis Workflow

Before writing, perform the following steps.

## Step 1: Read All Source Documents

Do not draft after reading only the first page or first document.

Determine whether later pages contain:

- eligibility details
- exceptions
- budget figures
- dates
- official quotations
- implementation conditions
- numerical results

---

## Step 2: Build a Fact Sheet

Internally organize the extracted facts using this structure:

```yaml
main_event:
announcement_date:
effective_date:
policy_context:
responsible_agency:
officials:
target_population:
measures:
  - measure:
    before:
    after:
    eligibility:
    amount:
    beneficiaries:
    expected_effect:
evidence:
  - metric:
    value:
    qualifier:
quotes:
  - speaker:
    title:
    text:
public_benefits:
next_steps:
public_action:
source_gaps:
```

The fact sheet is an internal drafting aid. Do not include it in the final article unless the user requests it.

---

## Step 3: Identify the News Angle

Reduce the announcement to one sentence:

"[Authority] is doing [action] for [target population], beginning [date], to achieve [main benefit]."

Example:

"健保署自8月起擴增多項藥品給付，預計提升癌症及重大疾病患者的治療可近性。"

This sentence determines the headline and lead.

---

## Step 4: Rank Facts by Importance

Use this order:

1. What happened
2. Who is affected
3. When it happens
4. Main public benefit
5. Largest or most important quantitative fact
6. Important implementation details
7. Supporting figures
8. Policy context
9. Official interpretation
10. Future direction or public action

Do not preserve source-document order if it produces a weak article.

The news release should use an inverted-pyramid structure.

---

# Article Structure

## 1. Headline

The headline should normally follow one of these patterns.

### Policy or Program Announcement

`[Main action or change] + [public benefit or policy significance]`

Examples of structure:

- 擴大○○給付 提升○○治療可近性
- 啟動○○計畫 強化○○照護
- 調升○○支付標準 支持○○醫療服務
- 新增○○服務 減輕○○負擔

### Results or Achievement

`[Result or recognition] + [broader significance]`

Examples:

- ○○成果登上國際期刊 健保創新制度獲國際肯定
- ○○計畫成效亮眼 強化在地照護量能

### Public Service

`[Service or feature] + [practical public benefit]`

Examples:

- 善用○○功能 掌握全家健康資訊
- ○○服務正式上線 就醫查詢更便利

### Warning or Scam Alert

`[Threat or incident] + [clear action instruction]`

Examples:

- 假冒健保通知詐騙再現 收到不明連結請勿點選
- 慎防○○詐騙 健保署提醒民眾先查證再操作

### Headline Rules

The headline should:

- state the news
- include the institution, policy, service, or affected group when useful
- communicate benefit or urgency
- remain factual
- avoid vague slogans
- avoid puns
- avoid exaggerated adjectives
- avoid clickbait
- avoid unsupported superlatives

Prefer:

"健保擴大癌症藥品給付 提升患者治療可近性"

Avoid:

"重大突破！癌症患者迎來全新希望"

---

# Lead Paragraph

The first paragraph must summarize the entire announcement.

Use this formula:

`Context or need → authority action → effective date or scale → affected population → main benefit`

A strong lead usually answers:

- What happened?
- Who did it?
- When does it happen?
- Who benefits?
- Why does it matter?
- What is the most important number?

The lead should normally be one paragraph.

Do not begin with a long history lesson.

Weak:

"隨著全球人口結構快速變化，各國政府近年來均十分重視醫療照護制度的發展..."

Better:

"為提升○○患者治療可近性，健保署自9月1日起新增○○治療健保給付，預估每年約8,000人受惠，年度挹注經費約4.2億元。"

---

# Policy Context Paragraph

After the lead, briefly explain why the policy, program, result, or warning matters.

Possible content:

- population aging
- unmet medical need
- existing reimbursement limitations
- patient access issues
- changes in clinical practice
- fraud patterns
- demand for digital services
- national healthcare policy direction

Keep the context concrete.

If the source mentions a larger policy initiative, connect the announcement to it only when relevant.

Do not force broad slogans into every release.

---

# Detail Paragraphs

Use one paragraph or paragraph group per major measure.

A useful internal pattern is:

`Measure → previous state → new state → number → affected population → expected effect`

Example structure:

"本次將○○支付點數由3,000點調升至6,000點，預估每年約5,800名患者受惠，年度增加支出約3,000萬元，以支持醫療院所提供更完整的○○照護。"

When several measures exist, organize them by:

- clinical importance
- size of affected population
- policy priority
- implementation order
- disease category
- service category

Do not produce an unreadable list of numbers without interpretation.

---

# Use of Numbers

Numbers are a core feature of this style.

Whenever supported by the source, include concrete figures such as:

- budget
- beneficiary count
- percentage
- number of institutions
- service volume
- payment points
- measured outcomes

Numbers should answer:

"How large is the change?"

Use comparison when useful:

- 原為 → 調整為
- 去年 → 今年
- 實施前 → 實施後
- 現行 → 新制

Do not manipulate figures to create stronger claims.

If the source says:

"從1,718點調升至6,839點"

write the exact values.

Do not convert it into:

"支付標準提高近4倍"

unless that calculation is accurate and the framing is useful.

If you calculate a percentage or multiplier yourself, label it carefully and verify the arithmetic.

---

# Authority Attribution

Institutional news commonly uses official attribution to explain significance.

Preferred forms:

- 健保署表示，...
- 健保署指出，...
- 健保署說明，...
- 健保署署長○○表示，...
- 衛生福利部○○表示，...

Use attribution for:

- policy purpose
- interpretation
- institutional commitment
- expected system impact
- future direction

Facts do not need attribution in every sentence if they are clearly presented as the institution's official announcement.

Do not overuse quotations.

One strong official statement is usually better than several repetitive ones.

---

# Public-Benefit Translation

After explaining administrative or technical changes, state what they mean for people.

Translate institutional action into practical consequences.

Examples:

Instead of:

"新增某藥品健保給付。"

Write:

"新增給付後，符合條件的患者可及早接受治療，降低自費負擔。"

Instead of:

"提高住院支付標準。"

Write:

"提高支付標準後，可支持醫療院所投入更多照護人力與資源。"

Only use benefits supported by the source or directly implied by the policy mechanism.

Avoid emotional overstatement.

---

# Closing Paragraph

The ending should perform one of four functions.

## 1. Future Direction

Use when the article announces policy development or results.

Pattern:

"健保署表示，未來將持續..."
"健保署將依實施成效..."
"後續將持續蒐集臨床資料..."

---

## 2. Public Action

Use for service announcements.

Pattern:

"民眾可透過..."
"符合資格者可自○月○日起..."
"如有疑問，可至○○網站查詢..."

---

## 3. Safety Instruction

Use for fraud, misinformation, or public warnings.

Pattern:

"如收到不明訊息，請勿點選連結..."
"如有疑問，應透過官方網站或客服專線查證..."

---

## 4. Institutional Commitment

Use when appropriate.

Pattern:

"健保署將持續以病人需求為核心..."
"持續提升醫療可近性及照護品質..."

Avoid empty slogans.

Tie the closing commitment to the specific topic of the article.

---

# Article Variants

The agent must first classify the announcement into one of the following variants.

---

## Variant A: Policy or Reimbursement Announcement

Use for:

- new coverage
- reimbursement changes
- payment adjustments
- new benefit items
- program expansion
- eligibility changes

Recommended structure:

1. Headline
2. Lead with change, date, beneficiaries, amount
3. Policy need
4. Major measure 1
5. Major measure 2
6. Additional measures
7. Official interpretation
8. Patient/public benefit
9. Future direction

---

## Variant B: Program Launch

Use for:

- pilot projects
- new service models
- new healthcare programs
- new digital tools

Recommended structure:

1. Headline
2. Problem or demand
3. Program launch and scope
4. How the program works
5. Participating institutions or users
6. Budget or expected volume
7. Expected benefits
8. Official statement
9. Participation or next-step information

---

## Variant C: Results or Research Achievement

Use for:

- pilot program outcomes
- statistical results
- research publications
- international recognition
- evaluation reports

Recommended structure:

1. Headline
2. Main result
3. Program background
4. Study or evaluation scope
5. Key metrics
6. Interpretation
7. International or policy significance
8. Official statement
9. Future implementation or research direction

Clearly distinguish correlation, observed outcomes, and causal claims.

Do not claim that a program "caused" an outcome unless the source supports causation.

---

## Variant D: Public Service or Digital Feature

Use for:

- Health Bank features
- online systems
- mobile services
- registration tools
- query functions

Recommended structure:

1. Headline
2. Public problem or scenario
3. Service introduction
4. Main functions
5. Usage statistics if available
6. Practical use case
7. How to access the service
8. Public reminder

A short user scenario may be used if the source contains one.

Do not invent testimonials.

---

## Variant E: Warning or Anti-Fraud Notice

Use for:

- scam warnings
- phishing
- fake SMS
- fake LINE messages
- misinformation
- impersonation

Recommended structure:

1. Headline with explicit warning
2. What is happening
3. What the fake message looks like
4. Why it is dangerous
5. What the institution does or does not do
6. What the public should do
7. Official verification channels

The most important instruction should appear near the top.

Use direct language.

---

## Variant F: Event News

Use for:

- ceremonies
- forums
- press conferences
- signing events
- award events
- institutional visits

Recommended structure:

1. Headline
2. What event occurred
3. Date and participants
4. Main announcement or outcome
5. Important statements
6. Relevant policy or program context
7. Public significance
8. Next step

Do not turn the article into a chronological event transcript.

Focus on the news outcome.

---

# Tone and Style

The writing should be:

- official
- factual
- calm
- clear
- public-facing
- accessible to non-specialists
- confident but not promotional
- specific
- concise

Use active constructions when natural.

Prefer short and medium-length sentences.

Avoid excessive bureaucratic nesting.

Use technical terms only when needed.

When a medical or administrative term may be unfamiliar, explain it briefly in plain language.

---

# Preferred Language Patterns

Useful institutional transitions include:

- 為提升...
- 為強化...
- 為減輕...
- 因應...
- 自○月○日起...
- 本次調整...
- 本次新增...
- 健保署表示...
- 健保署指出...
- 健保署說明...
- 預估每年...
- 預計約...
- 可望...
- 有助於...
- 進一步...
- 未來將持續...
- 民眾可透過...

Use these naturally. Do not repeat the same phrase in every paragraph.

---

# Language to Avoid

Avoid:

- 重大突破
- 空前
- 史上最強
- 震撼
- 驚人
- 奇蹟
- 劃時代
- 顛覆性
- 前所未有

unless the source explicitly supports the wording and the institution has used it officially.

Also avoid:

- emotional advocacy
- political praise
- self-congratulation
- speculative medical promises
- vague corporate language
- unexplained acronyms
- unnecessary English terms

---

# Handling Technical or Medical Material

When source documents contain technical medical information:

1. Preserve the medically correct terminology.
2. Explain the practical meaning in plain language.
3. Do not simplify away important eligibility or safety conditions.
4. Do not infer clinical effectiveness beyond the source.
5. Do not provide medical advice to individual readers.
6. Distinguish:
   - treatment availability
   - reimbursement eligibility
   - clinical recommendation
   - treatment effectiveness

These are not interchangeable.

Example:

"納入健保給付"

does not mean:

"所有患者都適合使用."

---

# Handling Multiple Sources

When several documents are provided:

## Source Priority

Prefer:

1. final official decision
2. final policy document
3. official implementation notice
4. approved meeting minutes
5. official statistical report
6. briefing slides
7. draft materials

If two sources conflict, use the most authoritative and recent source.

Do not silently merge conflicting numbers.

Flag unresolved conflicts.

---

## Duplicate Information

When multiple sources repeat the same figure, use it once.

Do not make the article longer merely because the same fact appears in multiple documents.

---

## Contradictions

If sources disagree on:

- date
- amount
- beneficiary count
- official title
- eligibility
- program scope

do not choose one arbitrarily.

Internally mark:

`SOURCE CONFLICT`

and either:

- resolve using a clearly authoritative source, or
- alert the user before publication

---

# Handling Missing Information

If a strong article can still be written without the missing field, omit it.

Example:

If the source gives no budget, do not write:

"健保署投入大筆經費."

Instead write:

"健保署自9月起新增○○給付."

If a missing fact is essential to avoid ambiguity, flag it.

Examples of essential missing facts:

- unclear effective date
- unclear target population
- unclear program eligibility
- contradictory payment amount
- missing identity of quoted official

---

# Quote Policy

Direct quotes must come from the source.

Never create a quotation that merely sounds appropriate.

If a source says:

"我們希望透過新制提升病人治療可近性。"

you may write:

健保署署長○○表示，「希望透過新制提升病人治療可近性。」

Do not write:

健保署署長○○表示，「這是台灣醫療制度的重要里程碑。」

unless those words or that clearly equivalent statement appear in the source.

When exact wording is not available, paraphrase with indirect attribution.

---

# Fact vs. Interpretation

The article should distinguish factual statements from institutional interpretation.

Fact:

"新制自9月1日起實施，預估每年約5,800人受惠。"

Interpretation:

"健保署表示，本次調整有助提升治療可近性。"

Avoid presenting interpretation as proven fact.

---

# Title Generation Process

Generate 3 internal headline candidates.

Evaluate each against:

- Is the main event clear?
- Does it identify the affected domain?
- Does it communicate public value?
- Does it avoid hype?
- Does it match the article's strongest fact?
- Is it concise enough for a government website?

Select the strongest one.

Do not show all headline candidates unless the user asks.

---

# Drafting Procedure

Follow this sequence.

## Phase 1: Extract

Build the fact sheet.

## Phase 2: Select

Determine:

- article variant
- main angle
- top 3 facts
- strongest quantitative evidence
- public benefit
- closing function

## Phase 3: Outline

Create an internal paragraph plan.

Example:

1. Lead: new reimbursement + date + annual beneficiaries
2. Context: current access problem
3. Measure A: payment change
4. Measure B: new covered item
5. Total budget and beneficiaries
6. Director-general interpretation
7. Patient benefit + next steps

## Phase 4: Draft

Write the full release.

## Phase 5: Compress

Remove:

- duplicate background
- repeated numbers
- generic government language
- unnecessary adjectives
- information already implied by the previous sentence

## Phase 6: Validate

Run the fact-check checklist below.

---

# Fact-Check Checklist

Before finalizing, verify every item.

## Dates

- [ ] Announcement date is correct.
- [ ] Effective date is correct.
- [ ] Event date is correct.
- [ ] No date has been inferred without evidence.

## Numbers

- [ ] Amounts match the source.
- [ ] Percentages match the source.
- [ ] Beneficiary counts match the source.
- [ ] Units are correct.
- [ ] Estimated figures are labeled as estimates.
- [ ] Annual and cumulative figures are not confused.
- [ ] Payment points and currency amounts are not confused.

## People and Institutions

- [ ] Names are spelled correctly.
- [ ] Titles are correct.
- [ ] Institutions are correctly named.
- [ ] Quotes are attributed to the correct speaker.

## Policy

- [ ] Eligibility conditions are correct.
- [ ] Program scope is correct.
- [ ] Previous and new rules are distinguished.
- [ ] Approval does not get confused with implementation.
- [ ] Coverage does not get confused with clinical recommendation.

## Claims

- [ ] No unsupported causal claim appears.
- [ ] No unsupported medical benefit appears.
- [ ] No unsupported future commitment appears.
- [ ] No invented quote appears.
- [ ] No invented public contact information appears.

---

# Editorial Quality Checklist

The final article should satisfy all of the following.

- [ ] The headline states the news.
- [ ] The first paragraph can stand alone as a summary.
- [ ] The first paragraph contains the most important fact.
- [ ] The article explains why the announcement matters.
- [ ] Important numbers appear early.
- [ ] Administrative changes are translated into public impact.
- [ ] Technical terms are understandable.
- [ ] Paragraphs have distinct functions.
- [ ] The article does not repeat the same claim several times.
- [ ] Quotes add interpretation instead of repeating facts.
- [ ] The ending gives a clear next step, direction, or public reminder.
- [ ] The tone remains institutional and factual.
- [ ] The article contains no unsupported information.

---

# Recommended Output Format

Unless the user asks for another format, output:

```markdown
# [Headline]

[Lead paragraph]

[Policy context paragraph]

[Main measure / detail paragraph]

[Additional measure / evidence paragraph]

[Official interpretation paragraph]

[Public benefit and closing paragraph]
```

Do not add labels such as:

"Lead"
"Background"
"Conclusion"

The final article should read as publication-ready prose.

---

# Optional Metadata

If the user requests publication metadata, provide it separately:

```yaml
title:
publication_date:
category:
keywords:
summary:
source_documents:
```

Do not mix metadata into the news article body.

---

# Length Guidance

Choose article length based on source density.

## Short Release

Use approximately 400-700 Chinese characters when:

- one change is announced
- only a few figures exist
- the article is a warning or simple service notice

## Standard Release

Use approximately 700-1,200 Chinese characters when:

- several measures are announced
- multiple beneficiary groups exist
- budget and policy context matter

## Long Release

Use approximately 1,200-1,800 Chinese characters only when:

- multiple measures require explanation
- the source contains significant evidence or research findings
- several institutions or implementation stages are relevant

Do not lengthen the release just to meet a target.

---

# Common Failure Modes

## Failure 1: Starting Too Broadly

Bad:

"隨著科技快速進步以及全球醫療環境不斷變化..."

Fix:

Start with the actual announcement.

---

## Failure 2: Copying the Source Document's Administrative Order

Source documents often begin with legal basis, committee history, or procedural details.

The article should begin with the public-facing news.

---

## Failure 3: Listing Numbers Without Meaning

Bad:

"投入4.7億元、受惠7.9萬人、調整6項支付標準."

Better:

Explain what each number changes for patients or providers.

---

## Failure 4: Inventing Human Impact

Bad:

"新制將讓所有患者重拾希望."

Fix:

Use only evidence-backed benefits such as:

"有助提升治療可近性."

---

## Failure 5: Fabricating Quotes

Never write a direct quote unless the source contains it.

---

## Failure 6: Excessive Slogans

Avoid ending every article with generic language such as:

"打造更完善、更溫暖、更有韌性的健康台灣."

Use concrete future actions instead.

---

## Failure 7: Confusing Estimate and Result

Do not turn:

"預估每年約1萬人受惠"

into:

"已有1萬人受惠."

---

## Failure 8: Confusing Payment and Clinical Guidance

A reimbursement change is a payment-policy decision.

It does not automatically mean:

- the treatment is newly medically recommended
- every patient should receive it
- the treatment is clinically superior

---

# Final Self-Review

Before responding, ask:

1. Can a reader understand the announcement from the headline and first paragraph alone?
2. Is every number supported by the source?
3. Did I identify who benefits?
4. Did I explain the change instead of merely naming it?
5. Did I translate the administrative action into practical public value?
6. Did I distinguish facts from official interpretation?
7. Did I avoid inventing quotes or claims?
8. Does the final paragraph tell the reader what happens next?
9. Does this sound like an official institutional release rather than a reporter's article?
10. Could any sentence create a misleading impression if read by itself?

If any answer is no, revise before finalizing.

---

# Compact Mental Model

Use this as the core framework:

```text
NEWS
What happened?

WHY
Why is it needed?

ACTION
What exactly did the institution change or do?

EVIDENCE
What numbers, results, dates, or scope prove the scale?

AUTHORITY
How does the responsible authority interpret the change?

IMPACT
What does this mean for patients, providers, families, or the public?

NEXT
What happens next, or what should the public do?
```

For most policy releases, the final narrative should feel like:

```text
There is a healthcare or public-service need.
→ The institution recognized it.
→ A concrete action was taken.
→ The scale is supported with specific figures.
→ The responsible authority explains the policy significance.
→ The article translates the action into public benefit.
→ The release ends with a next step, commitment, or instruction.
```

This structure should remain stable even when the subject matter changes.
