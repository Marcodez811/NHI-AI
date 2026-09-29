import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api/client";
import type { AgentEvent, AgentRunSnapshot } from "../lib/api/agents";
import * as api from "../lib/api/agents";
import { useAgentDevRuns } from "../lib/hooks/useAgentDevRuns";

vi.mock("../lib/api/agents", async () => {
    const actual = await vi.importActual<typeof import("../lib/api/agents")>("../lib/api/agents");
    return {
        ...actual,
        fetchAgentRuns: vi.fn(),
        fetchAgentRun: vi.fn(),
        fetchAgentRunEvents: vi.fn(),
    };
});

const snapshot = (overrides: Partial<AgentRunSnapshot> = {}): AgentRunSnapshot => ({
    run_id: "run-1",
    workflow: "slides",
    status: "running",
    phase: "drafting",
    runner: "codex",
    task_id: "task-1",
    worker_id: "worker-1",
    started_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:01Z",
    finished_at: null,
    duration_ms: null,
    message: "Drafting presentation",
    last_heartbeat_at: null,
    last_sequence: 0,
    nodes: [],
    ...overrides,
});

const event = (sequence: number, runId = "run-1"): AgentEvent => ({
    run_id: runId,
    workflow: "slides",
    node_id: null,
    agent_role: null,
    runner: "codex",
    model: "gpt-5.6-luna",
    reasoning_effort: "high",
    worker_id: "worker-1",
    attempt: null,
    sequence,
    event_type: "phase_changed",
    status: "running",
    phase: "drafting",
    message: `Event ${sequence}`,
    occurred_at: "2026-01-01T00:00:01Z",
    duration_ms: null,
    metadata: {},
});

describe("useAgentDevRuns", () => {
    beforeEach(() => {
        vi.resetAllMocks();
        vi.mocked(api.fetchAgentRuns).mockResolvedValue({ runs: [snapshot()] });
        vi.mocked(api.fetchAgentRun).mockResolvedValue(snapshot());
        vi.mocked(api.fetchAgentRunEvents).mockResolvedValue({
            events: [],
            after: 0,
            next_after: null,
        });
    });

    it("selects the first run and loads its detail from the initial cursor", async () => {
        const { result } = renderHook(() =>
            useAgentDevRuns({ pollIntervalMs: 500, eventLimit: 25 }),
        );

        await waitFor(() => expect(result.current.loading).toBe(false));

        expect(result.current.selectedRunId).toBe("run-1");
        expect(result.current.snapshot?.run_id).toBe("run-1");
        expect(api.fetchAgentRunEvents).toHaveBeenCalledWith(
            "run-1",
            expect.objectContaining({ after: 0, limit: 25 }),
        );
    });

    it("advances the event cursor and deduplicates overlap between polls", async () => {
        vi.mocked(api.fetchAgentRunEvents)
            .mockResolvedValueOnce({
                events: [event(1), event(2)],
                after: 0,
                next_after: 2,
            })
            .mockResolvedValueOnce({
                events: [event(2), event(3)],
                after: 2,
                next_after: 3,
            });
        const { result } = renderHook(() => useAgentDevRuns({ pollIntervalMs: 500 }));

        await waitFor(() => expect(result.current.events).toHaveLength(2));
        await act(async () => {
            await result.current.refresh();
        });

        expect(api.fetchAgentRunEvents).toHaveBeenLastCalledWith(
            "run-1",
            expect.objectContaining({ after: 2, limit: 200 }),
        );
        expect(result.current.events.map((item) => item.sequence)).toEqual([1, 2, 3]);
    });

    it("advances past old progress and heartbeat events without adding them to the lifecycle timeline", async () => {
        const current = snapshot({ last_sequence: 3 });
        vi.mocked(api.fetchAgentRuns).mockResolvedValue({ runs: [current] });
        vi.mocked(api.fetchAgentRun).mockResolvedValue(current);
        vi.mocked(api.fetchAgentRunEvents).mockResolvedValue({
            events: [
                { ...event(1), event_type: "node_progress" },
                { ...event(2), event_type: "heartbeat" },
                event(3),
            ],
            after: 0,
            next_after: 3,
        });
        const { result } = renderHook(() => useAgentDevRuns({ pollIntervalMs: 500 }));

        await waitFor(() => expect(result.current.events).toHaveLength(1));
        expect(result.current.events[0].sequence).toBe(3);
        await act(async () => {
            await result.current.refresh();
        });
        expect(api.fetchAgentRunEvents).toHaveBeenLastCalledWith(
            "run-1",
            expect.objectContaining({ after: 3 }),
        );
    });

    it("drains every event page before treating a terminal run as synchronized", async () => {
        const terminal = snapshot({ status: "failed", phase: "failed", last_sequence: 450 });
        vi.mocked(api.fetchAgentRuns).mockResolvedValue({ runs: [terminal] });
        vi.mocked(api.fetchAgentRun).mockResolvedValue(terminal);
        vi.mocked(api.fetchAgentRunEvents).mockImplementation(async (_runId, params) => {
            const after = params?.after ?? 0;
            const first = after + 1;
            const last = Math.min(first + 199, 450);
            const events = first <= 450
                ? Array.from({ length: last - first + 1 }, (_, index) => event(first + index))
                : [];
            return { events, after, next_after: events.length ? last : after };
        });

        const { result } = renderHook(() => useAgentDevRuns({ pollIntervalMs: 500 }));

        await waitFor(() => expect(result.current.events).toHaveLength(450));

        expect(vi.mocked(api.fetchAgentRunEvents).mock.calls.map((call) => call[1]?.after)).toEqual([
            0,
            200,
            400,
        ]);
    });

    it("manually refreshes selected terminal-run details after background polling stops", async () => {
        const terminal = snapshot({ status: "failed", phase: "failed", last_sequence: 1 });
        vi.mocked(api.fetchAgentRuns).mockResolvedValue({ runs: [terminal] });
        vi.mocked(api.fetchAgentRun).mockResolvedValue(terminal);
        vi.mocked(api.fetchAgentRunEvents).mockImplementation(async (_runId, params) => {
            const after = params?.after ?? 0;
            return after === 0
                ? { events: [event(1)], after, next_after: 1 }
                : { events: [], after, next_after: after };
        });
        const { result } = renderHook(() => useAgentDevRuns({ pollIntervalMs: 500 }));

        await waitFor(() => expect(result.current.events).toHaveLength(1));
        vi.mocked(api.fetchAgentRun).mockClear();
        vi.mocked(api.fetchAgentRunEvents).mockClear();

        await act(async () => {
            await result.current.refresh();
        });

        expect(api.fetchAgentRun).toHaveBeenCalledOnce();
        expect(api.fetchAgentRunEvents).toHaveBeenCalledWith(
            "run-1",
            expect.objectContaining({ after: 1, limit: 200 }),
        );
    });

    it("resets telemetry after switching runs and surfaces list errors", async () => {
        const first = snapshot();
        const second = snapshot({ run_id: "run-2", task_id: "task-2" });
        vi.mocked(api.fetchAgentRuns).mockResolvedValue({ runs: [first, second] });
        vi.mocked(api.fetchAgentRun).mockImplementation(async (runId) =>
            runId === "run-2" ? second : first,
        );
        vi.mocked(api.fetchAgentRunEvents).mockImplementation(async (runId, params) => ({
            events: [event(1, runId)],
            after: params?.after ?? 0,
            next_after: 1,
        }));
        const { result } = renderHook(() => useAgentDevRuns({ pollIntervalMs: 500 }));

        await waitFor(() => expect(result.current.events).toHaveLength(1));
        act(() => result.current.selectRun("run-2"));
        await act(async () => {
            await result.current.refresh();
        });

        expect(result.current.selectedRunId).toBe("run-2");
        expect(result.current.snapshot?.run_id).toBe("run-2");
        expect(result.current.events).toEqual([event(1, "run-2")]);

        vi.mocked(api.fetchAgentRuns).mockRejectedValueOnce(
            new ApiError(503, "Agent telemetry is temporarily unavailable."),
        );
        await act(async () => {
            await result.current.refresh();
        });
        expect(result.current.error).toMatchObject({
            status: 503,
            message: "Agent telemetry is temporarily unavailable.",
        });
    });
});
