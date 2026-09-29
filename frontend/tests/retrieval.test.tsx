import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RetrievalStatus } from "../lib/api/retrieval";
import { useRetrievalStatus } from "../lib/hooks/useRetrievalStatus";

vi.mock("../lib/api/retrieval", () => ({
    fetchRetrievalStatus: vi.fn(),
}));

import * as retrievalApi from "../lib/api/retrieval";

const status = (overrides: Partial<RetrievalStatus> = {}): RetrievalStatus => ({
    state: "ready",
    can_retrieve: true,
    ready_document_count: 1,
    error_code: null,
    warning_code: null,
    ...overrides,
});

describe("API error contracts", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("reads the sanitized bootstrap status endpoint", async () => {
        const payload = status({ state: "provisioning", can_retrieve: false, ready_document_count: 0 });
        const fetchMock = vi.fn(async () =>
            new Response(JSON.stringify(payload), {
                status: 200,
                headers: { "Content-Type": "application/json" },
            }),
        );
        vi.stubGlobal("fetch", fetchMock);

        const { fetchRetrievalStatus } = await vi.importActual<typeof import("../lib/api/retrieval")>(
            "../lib/api/retrieval",
        );
        await expect(fetchRetrievalStatus()).resolves.toEqual(payload);
        expect(fetchMock).toHaveBeenCalledWith(
            "/api/v1/retrieval/status",
            expect.objectContaining({ headers: expect.any(Headers) }),
        );
    });
});

describe("useRetrievalStatus", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.mocked(retrievalApi.fetchRetrievalStatus).mockReset();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("polls while the backend is provisioning and stops once ready", async () => {
        vi.mocked(retrievalApi.fetchRetrievalStatus)
            .mockResolvedValueOnce(status({ state: "provisioning", can_retrieve: false, ready_document_count: 0 }))
            .mockResolvedValueOnce(status());

        const { result } = renderHook(() =>
            useRetrievalStatus({ pollIntervalMs: 100 }),
        );

        await act(async () => {
            await Promise.resolve();
        });
        expect(result.current.status?.state).toBe("provisioning");

        await act(async () => {
            await vi.advanceTimersByTimeAsync(100);
        });
        expect(result.current.status?.state).toBe("ready");

        await act(async () => {
            await vi.advanceTimersByTimeAsync(500);
        });
        expect(retrievalApi.fetchRetrievalStatus).toHaveBeenCalledTimes(2);
    });
});
