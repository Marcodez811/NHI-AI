# Chat harness options

**Date:** 2026-09-26. **Status:** reading material for a decision, not a plan.
**Question:** which harness should power the agentic chat? It must support multiple
providers, stay minimal, and let us build our own skills and workflows that the user or the
agent can trigger (like Claude Cowork or ChatGPT agent mode), ending in an output such as a
report.

---

## 1. Short answer

**Start with the OpenAI Agents SDK, which we already run, plus a thin skill layer of our
own in the open `SKILL.md` format. Keep the harness behind one small interface so it can be
swapped later.**

- It is already in the backend (`app/services/agentic/sdk_runner.py`), and it already runs
  the planner on Gemini through LiteLLM. Multi-provider support is proven in our own
  deployment, not a promise from a web page.
- It gives us the loop, tool calling, streaming, MCP support and structured output
  out of the box. That is the "minimal" we need; we don't have to write the loop ourselves.
- Skills don't need a framework. A skill is a folder with a `SKILL.md`. Loading one is about
  a hundred lines of our own code, and because the format is an open standard, our skills
  keep working if we change harness.

**pi** is the cleanest harness design of the options, but it is TypeScript. From Python it
only works as a Node subprocess over RPC, and it is built as a *coding* agent, so it
arrives with shell and file-editing tools we would have to strip out. A Python port exists,
but it is unofficial. Good reference design; not our runtime for now.

**Pydantic AI** is the serious alternative if LiteLLM's translation layer ever gets in the
way (details in section 4B).

---

## 2. Vocabulary (quick refresher)

| Term | Meaning here |
| --- | --- |
| **Harness** | The code around the model: the loop that sends messages and tools, runs tool calls, feeds results back, streams output, saves the conversation. |
| **Agent loop** | model → wants a tool? → run it → append result → model again … → final answer. |
| **Tool** | A function the model may call, e.g. `search_knowledge_base(query)`. We write these. |
| **Skill** | A packaged task: instructions (+ optional reference files and templates). The agent sees each skill's *name and description* up front, and loads the full instructions only when it needs them ("progressive disclosure"). |
| **Workflow** | A skill with fixed steps and a defined output (e.g. "立法院答詢報告": gather sources → outline → user approves → write report). |
| **Session** | A saved conversation the user can come back to. |
| **Artifact** | The deliverable (report) shown beside the chat, editable, exportable. |
| **MCP** | Model Context Protocol: a standard way to plug external tools/data sources into any agent. Useful later for "connectors". |

---

## 3. What we need from a harness

Must have:

1. **Multi-provider:** OpenAI, Gemini and Anthropic at minimum, chosen per chat (model
   picker in the chat box).
2. **Tool calling + streaming:** show "搜尋知識庫中…" and stream text as it arrives, over our
   existing SSE transport.
3. **Structured output:** a report skill must be able to return a typed outline or report,
   like the slides planner does today.
4. **Python, in-process:** our backend, knowledge base, auth rules and "no internal ids to
   users" rules are all Python. Crossing a process or language boundary on every turn adds
   failure modes.
5. **Our own skills:** triggered by the user (picker or `/skill`) or chosen by the agent
   from their descriptions.
6. **Session persistence** in our Postgres.
7. **No shell or filesystem access by default.** Chat tools are our Python functions, not
   bash. (This avoids the sandbox problem we hit with the author.)

Nice to have: MCP for future connectors, human-in-the-loop approval steps, token usage
reporting for the dashboard.

---

## 4. The options

### A. OpenAI Agents SDK + LiteLLM (already in use)

- **Multi-provider:** yes, through its LiteLLM integration (`litellm/<provider>/<model>`).
  Verified here: the planner runs on `litellm/gemini/gemini-3.8-flash`.
- **Minimal:** fairly. `Agent`, `Runner`, tools as decorated Python functions, streamed runs,
  guardrails, handoffs, MCP. We use the parts we need.
- **Skills:** the SDK has a Skills capability, but it lives inside its **sandbox** feature,
  which discovers `SKILL.md` files in sandbox directories. We already learned that the
  sandbox's local backend is not safe on Linux. So we'd implement skills ourselves (section 5),
  which is small and avoids the sandbox entirely.
- **Sessions:** the SDK has session support for conversation memory. Before relying on it,
  verify the SQL-backed session type against the SDK version we pin. We may simply store
  messages in our own table instead.
- **Risks:**
  - It's OpenAI-first. Provider-specific features (reasoning settings, prompt caching,
    citations) go through LiteLLM's translation and can lag or differ per provider.
  - We already hit one Gemini schema quirk (`maxItems`).
- **Cost to adopt:** lowest. Same dependency and patterns as the planner.

### B. Pydantic AI (+ `pydantic-ai-skills`)

- **Multi-provider:** yes, *natively*. Each provider has its own adapter (OpenAI, Anthropic,
  Gemini, Bedrock, Mistral, Groq, Ollama, LiteLLM…), with no translation layer in between.
- **Minimal:** yes. Typed agents, tools, structured output and streaming; very Pythonic, and
  it fits naturally with our Pydantic/SQLModel code.
- **Skills:** the community package `pydantic-ai-skills` implements the `SKILL.md` standard
  with progressive disclosure. It's not official Pydantic, so treat it as optional.
- **Risks:** a second agent framework next to the Agents SDK (which the workflows use). Two
  ways of doing the same thing is exactly the complexity we're trying to reduce.
- **When to pick it:** if LiteLLM's translation causes repeated provider-specific bugs in chat,
  or if we later move the workflows off the Agents SDK too.

### C. pi (via RPC)

- **What it is:** a minimal TypeScript agent toolkit: a unified LLM API (`pi-ai`), an agent
  core, and a coding-agent CLI. It has four modes: interactive, print, **RPC (JSON over
  stdin/stdout)** and an in-process Node SDK. Well designed, with sessions, extensions and
  skills.
- **From Python:** only as a subprocess over RPC: spawn `pi --mode rpc`, send JSON commands,
  read JSON events.
- **Risks:**
  - It's a Node process per session: more memory, lifecycle management, crash handling.
  - Its built-in tools are coding tools (read, write, edit, bash). We'd have to disable them
    and expose our Python tools *back* across the process boundary, which is awkward in
    the direction we need.
  - Every "no leakage" and access rule would have to be enforced on both sides.
- **Python port:** `GuinasCode/python-pi` ports pi's packages (`pi_ai`, `pi_agent_core`,
  sessions, SQLite storage). It's **unofficial**, and its maturity and maintenance are
  unknown. Don't build the product on it; reading its code is fine.
- **When to pick it:** if the chat backend ever becomes a separate Node/TypeScript service,
  e.g. running next to the Next.js frontend.

### D. Our own loop directly on LiteLLM

- **What it is:** about 300 lines: call `litellm.acompletion` with tools, run tool calls,
  loop, stream.
- **Pros:** full control, nothing hidden.
- **Cons:** we'd rewrite streaming tool-call assembly, parallel tool calls, retries, structured
  output and usage accounting. This is what A and B already do, and do better.
- **Verdict:** only as a teaching exercise.

### E. Heavier or single-vendor options (not recommended now)

| Option | Why not now |
| --- | --- |
| LangGraph / LangChain | Powerful graph orchestration, but a lot of abstraction for what we need. Worth it only for complex long-running multi-step graphs. |
| Google ADK | Good, but Gemini-first. |
| Strands Agents (AWS) | Multi-provider and minimal, but Bedrock-first. It offers no advantage over A for us. |
| Claude Agent SDK | Claude only, and it drives a CLI process. Not multi-provider. |
| smolagents (Hugging Face) | Built around "code agents" that write and run Python, which we don't want in chat. |

### Comparison

| | A. Agents SDK | B. Pydantic AI | C. pi (RPC) | D. Own loop |
| --- | --- | --- | --- | --- |
| Python, in-process | ✓ | ✓ | ✗ (Node subprocess) | ✓ |
| Multi-provider | ✓ via LiteLLM | ✓ native | ✓ native | ✓ via LiteLLM |
| Already in our code | ✓ | ✗ | ✗ | partly |
| Skills | our own layer | community package / own | built in | own |
| Structured output | ✓ | ✓ | ✓ | build it |
| MCP | ✓ | ✓ | via extensions | build it |
| Extra moving parts | none | new framework | Node runtime + protocol | lots of code |

---

## 5. Skills: build our own layer (whichever harness we pick)

Use the open **Agent Skills** format (`SKILL.md` with YAML front matter), so skills are
portable across harnesses. Anthropic published it as a standard, and OpenAI, Microsoft,
GitHub, Cursor and others have adopted it.

```
backend/chat_skills/
  legislative-briefing/
    SKILL.md          # name, description, instructions, output shape
    template.md       # report section template
    examples/…        # optional reference material
```

```markdown
---
name: legislative-briefing
description: 為立法院質詢準備答詢參考資料報告。當使用者需要彙整某議題的背景、數據與回應要點時使用。
output: report
---
## Steps
1. Ask which issue and which committee, if not given.
2. Search the knowledge base; list the sources you will use.
3. Propose an outline; wait for the user to approve it.
4. Write the report following template.md …
```

How it works at runtime:

1. **Discovery:** at session start, the agent's instructions list each skill's `name` and
   `description` only (cheap).
2. **Trigger:** either the user picks a skill in the chat box (like choosing a GPT), or the
   agent calls a `load_skill(name)` tool when a description matches the request.
3. **Load:** `load_skill` returns the full `SKILL.md` body. Reference files come through a
   `read_skill_file(name, path)` tool, only if needed.
4. **Tools stay ours:** a skill can *request* tools (e.g. `search_knowledge_base`,
   `create_report`), but the harness decides which tools exist. **No skill runs scripts in
   chat.** That keeps us out of the sandbox problem.
5. **Output:** a report skill ends by calling `create_report(...)` with structured content,
   which the frontend renders in the artifact panel and can export to Word/PDF.

This is the same pattern as the slides planner (discuss → approve → produce), generalized.

---

## 6. Suggested architecture

```
Next.js chat UI ──SSE──▶ FastAPI /chat/sessions/{id}/messages
                              │
                        ChatEngine (our interface)
                              │  implemented by
                        AgentsSdkChatEngine  ← swap point (Pydantic AI / pi later)
                              │
            ┌─────────────────┼──────────────────┐
         Tools            Skill loader       Session store
  search_knowledge_base   SKILL.md files     Postgres tables
  read_document           load_skill         (sessions, messages,
  create_report           read_skill_file     artifacts)
```

- **`ChatEngine`** is a small interface: `run_turn(session, message, model) -> async stream of
  events`. Everything else (routes, UI, storage, skills, tools) depends only on it. Switching
  harness later means rewriting one class, not the app.
- **Long workflows** (slides, news) stay as background jobs. A skill can start one, and the
  chat shows its progress, reusing the job system and telemetry we already have.
- **Model picker:** the chat box sends the model per message. The server validates it with
  the same rules as the settings page (known provider, key configured).

---

## 7. Open questions (need PM/customer input)

1. Which reports first? One real example with its sections decides the first skill.
2. Sources: knowledge base only, or web or live data too? (Web search is a tool and a cost
   decision.)
3. Output: read in the app, or Word/PDF following an official template?
4. Users: multiple staff with private histories means login, which we have skipped so far.
5. Do the existing chat modes (立法院答詢 / 輿情 / 備參) become the first skills?
6. Retrieval: the current chat uses OpenAI's hosted vector store (`file_search`), which is
   OpenAI-only. Multi-provider chat needs knowledge-base search as **our own tool**, either
   calling that vector store from Python or our own index. This is a real piece of work,
   and the one most likely to surprise us.

---

## 8. Next step

After the PM answers: a phased plan in `docs/` (same style as the other briefs), starting
with Phase 1 = sessions + `ChatEngine` on the Agents SDK + knowledge-base tool + model
picker, with no skills yet. That proves the loop and multi-provider streaming before we
add skills and reports.

---

## Sources

- pi RPC mode and SDK:
  [rpc.md](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/rpc.md),
  [sdk.md](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/sdk.md),
  [coding-agent README](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md)
- Unofficial Python port: [GuinasCode/python-pi](https://github.com/GuinasCode/python-pi)
- Agent Skills for Pydantic AI:
  [pydantic-ai-skills](https://github.com/DougTrajano/pydantic-ai-skills),
  [docs](https://dougtrajano.github.io/pydantic-ai-skills/)
- OpenAI Agents SDK:
  [docs](https://openai.github.io/openai-agents-python/),
  [sandbox Skills capability](https://openai.github.io/openai-agents-python/ref/sandbox/capabilities/skills/),
  [Skills guide](https://developers.openai.com/api/docs/guides/tools-skills)

Claims about the Agents SDK and LiteLLM come from running them in this codebase. Claims
about Pydantic AI, pi and python-pi come from their documentation and repositories, not
from our own tests.
