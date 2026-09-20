# Guide: migrate the existing workflow to Agents SDK

## 1. Start at the runner boundary

Your existing `AgentRunner` interface already accepts an `AgentExecutionRequest` and returns an `AgentExecutionResult`. Implement an SDK-backed runner with that same interface.

Inside it, follow the pattern from your example:

1. Build an `Agent` for the requested stage.
2. Supply its instructions, model settings, and tools.
3. Execute it through `Runner`.
4. Translate its output, usage, and events into your existing result contract.

Map the current request fields as follows:

| Existing field | SDK implementation |
|---|---|
| `role` | Agent name and stage configuration |
| `model`, `reasoning_effort` | Agent model and `ModelSettings` |
| `prompt` | Input to the run |
| `skill_names` | Load the declared skill instructions and supporting resources |
| `output_schema` | Structured output configuration |
| `workspace` and path restrictions | Enforced tool execution context |
| `progress_callback` | Translate streamed SDK events |

For structured reviewer output, use a Pydantic output type and serialize the result into the existing response contract. Keep the current application validation.

## 2. Give each agent its actual capabilities

An `Agent` with a prompt cannot automatically read local documents or produce a PPTX. Supply explicit tools:

| Stage | Required capabilities |
|---|---|
| Extraction | Read staged sources, run extraction utilities, write extraction artifacts |
| Author | Read frozen evidence, write presentation code/files, run rendering utilities, inspect rendered images |
| Reviewer | Read evidence and validated deck content/images; return structured findings |

Use `function_tool` for Python-backed tools; that decorator is present in your installed SDK.

Build tools around the existing utilities and workspace conventions. For command execution, adapt the current process-isolation mechanism to launch tool subprocesses. Enforce file restrictions on every access path, including commands.

Load the relevant `SKILL.md` instructions explicitly. Merely placing skills in `.agents/skills` does not make a basic `Agent(...)` invocation use them.

**First milestone:** one SDK author can generate and render a real PPTX from an existing frozen evidence fixture.

## 3. Connect the agents through your existing coordinator

Keep the sequence shown in your diagram:

**Prepare → extract → freeze evidence → author → validate → review → revise or publish**

Replace the agent invocations within that sequence. Your coordinator already handles stage order, validation failures, review findings, and bounded retries.

Use separate SDK runs for extraction, authoring, and reviewing. Give reviewers their own context; pass revision feedback explicitly to the next author attempt.

You do not need agent handoffs for this fixed pipeline. Explicit Python orchestration makes its mandatory stages predictable. The SDK supports both handoffs and specialists used as tools, depending on who should control execution. [Official orchestration guidance](https://developers.openai.com/api/docs/guides/agents/orchestration)

Keep the planner in your example for the later outline-interaction phase.

## 4. Integrate production execution and chat

Register the new runner in the worker and switch the slides and news adapters to it. Check default runner names and legacy branches so no production stage accidentally continues through Codex.

Use `Runner.run_streamed` where progress is needed. Translate its events into your existing application events; preserve deadlines, cancellation, safe errors, and audit metadata.

For chat:

- Create a QA agent using the current mode-specific instructions.
- Configure `FileSearchTool` with the existing vector store, metadata filters, and server-resolved document IDs.
- Normalize citations from SDK response items.
- Preserve the current response and streaming formats.
- Keep each request independent during this phase.

An SDK run can execute multiple model/tool steps within one application turn; saved conversation history is a separate integration. [Official running-agents guidance](https://developers.openai.com/api/docs/guides/agents/running-agents)

## 5. Verify, then remove Codex

Work through these checkpoints:

1. SDK author creates a valid, rendered PPTX from frozen evidence.
2. SDK extraction produces evidence accepted by existing validators.
3. SDK reviewer returns valid findings and triggers an author correction.
4. Slides and news finish through the real worker and publish their existing outputs.
5. Chat retains citations, source filtering, streaming, and insufficient-evidence behavior.
6. Timeouts terminate tools; reviewers cannot write; agents cannot access another job’s files.
7. Remove Codex imports, runtime paths, dependency, and obsolete configuration after these checks pass.

The finished phase should reproduce today’s workflow through Agents SDK. It introduces no new public endpoints, database schema, saved conversations, or outline approval flow.
