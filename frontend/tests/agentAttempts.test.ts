import { describe, expect, it } from "vitest";
import type { AgentEvent, AgentNodeSnapshot } from "../lib/api";
import { deriveAgentAttempts } from "../components/dev/agent/attempts";

const event = (
    sequence: number,
    eventType: AgentEvent["event_type"],
    occurredAt: string,
    attempt: number,
    durationMs: number | null = null,
): AgentEvent => ({
    run_id: "run-1",
    workflow: "slides",
    node_id: "author",
    agent_role: "author",
    runner: "codex",
    model: "gpt-5.6-luna",
    reasoning_effort: "high",
    worker_id: "worker-1",
    attempt,
    sequence,
    event_type: eventType,
    status: "running",
    phase: "drafting",
    message: null,
    occurred_at: occurredAt,
    duration_ms: durationMs,
    metadata: {},
});

const node: AgentNodeSnapshot = {
    node_id: "author",
    agent_role: "author",
    runner: "codex",
    model: "gpt-5.6-luna",
    reasoning_effort: "high",
    status: "running",
    attempt: 2,
    task_id: "task-1",
    worker_id: "worker-1",
    provider_run_id: null,
    started_at: "2026-01-01T00:00:10Z",
    updated_at: "2026-01-01T00:00:11Z",
    finished_at: null,
    duration_ms: null,
    message: null,
    last_heartbeat_at: "2026-01-01T00:00:11Z",
};

describe("deriveAgentAttempts", () => {
    it("keeps completed attempts and a live latest attempt separate", () => {
        const attempts = deriveAgentAttempts(
            [
                event(1, "node_started", "2026-01-01T00:00:00Z", 1),
                event(2, "node_completed", "2026-01-01T00:00:05Z", 1, 5_000),
                event(3, "node_started", "2026-01-01T00:00:10Z", 2),
            ],
            [node],
            new Date("2026-01-01T00:00:13Z").valueOf(),
        ).get("author");

        expect(attempts).toMatchObject([
            { attempt: 1, status: "completed", durationMs: 5_000 },
            { attempt: 2, status: "running", durationMs: 3_000 },
        ]);
    });
});
