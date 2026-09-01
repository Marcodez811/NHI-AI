import { describe, expect, it, vi } from "vitest";
import {
    fetchAgentRun,
    fetchAgentRunEvents,
    fetchAgentRuns,
} from "../lib/api";

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
    });
}

describe("agent telemetry API contracts", () => {
    it("uses the gated run and incremental-event paths with encoded parameters", async () => {
        const fetchMock = vi.fn(async () =>
            jsonResponse({ runs: [], events: [], after: 0, next_after: null }),
        );
        vi.stubGlobal("fetch", fetchMock);
        const controller = new AbortController();

        await fetchAgentRuns(25, { signal: controller.signal });
        await fetchAgentRun("run / one", { signal: controller.signal });
        await fetchAgentRunEvents("run / one", {
            after: 7,
            limit: 20,
            signal: controller.signal,
        });

        expect(fetchMock).toHaveBeenNthCalledWith(
            1,
            "/api/v1/dev/agent-runs?limit=25",
            expect.objectContaining({ signal: controller.signal }),
        );
        expect(fetchMock).toHaveBeenNthCalledWith(
            2,
            "/api/v1/dev/agent-runs/run%20%2F%20one",
            expect.objectContaining({ signal: controller.signal }),
        );
        expect(fetchMock).toHaveBeenNthCalledWith(
            3,
            "/api/v1/dev/agent-runs/run%20%2F%20one/events?after=7&limit=20",
            expect.objectContaining({ signal: controller.signal }),
        );
    });

    it("preserves unavailable and missing telemetry responses as ApiError values", async () => {
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce(jsonResponse({ detail: "Agent run was not found." }, 404))
            .mockResolvedValueOnce(jsonResponse({ detail: "Agent telemetry is temporarily unavailable." }, 503));
        vi.stubGlobal("fetch", fetchMock);

        await expect(fetchAgentRun("missing")).rejects.toMatchObject({
            status: 404,
            message: "Agent run was not found.",
        });
        await expect(fetchAgentRuns()).rejects.toMatchObject({
            status: 503,
            message: "Agent telemetry is temporarily unavailable.",
        });
    });
});
