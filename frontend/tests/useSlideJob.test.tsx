import { act, renderHook } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import {
    ApiError,
    type CreateSlidePayload,
    type SlideJob,
} from "../lib/api";
import * as api from "../lib/api";
import { useSlideJob } from "../lib/hooks/useSlideJob";

vi.mock("../lib/api", async () => {
    const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
    return {
        ...actual,
        createSlideJob: vi.fn(),
        getSlideJob: vi.fn(),
    };
});

const payload: CreateSlidePayload = {
    title: "政策簡報",
    document_ids: ["doc-1"],
    slides_count: 8,
    guidance: "保持精簡。",
    tone: "formal",
};

const job = (overrides: Partial<SlideJob> = {}): SlideJob => ({
    job_id: "job-1",
    status: "running",
    phase: "drafting",
    stage: "working",
    message: "簡報生成中",
    started_at: null,
    finished_at: null,
    error: null,
    download_url: null,
    ...overrides,
});

describe("useSlideJob", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.resetAllMocks();
        window.sessionStorage.clear();
        vi.mocked(api.createSlideJob).mockResolvedValue({
            job_id: "job-1",
            status: "queued",
            phase: "queued",
        });
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("backs off after transient failures and resumes polling after recovery", async () => {
        vi.mocked(api.getSlideJob)
            .mockRejectedValueOnce(new ApiError(503, "狀態暫時無法取得"))
            .mockResolvedValueOnce(job())
            .mockResolvedValueOnce(job({ status: "completed", stage: "completed", message: "完成" }));
        const { result } = renderHook(() =>
            useSlideJob({ pollIntervalMs: 250, maxPollIntervalMs: 500 }),
        );

        await act(async () => {
            await result.current.startJob(payload);
        });
        expect(result.current.phase).toBe("polling");
        expect(api.getSlideJob).not.toHaveBeenCalled();

        await act(async () => {
            await vi.advanceTimersByTimeAsync(250);
        });
        expect(result.current.warning).toBe("Status temporarily unavailable—retrying.");
        expect(result.current.consecutivePollFailures).toBe(1);

        await act(async () => {
            await vi.advanceTimersByTimeAsync(249);
        });
        expect(api.getSlideJob).toHaveBeenCalledTimes(1);
        await act(async () => {
            await vi.advanceTimersByTimeAsync(1);
        });
        expect(result.current.warning).toBeNull();
        expect(result.current.consecutivePollFailures).toBe(0);

        await act(async () => {
            await vi.advanceTimersByTimeAsync(250);
        });
        expect(result.current.phase).toBe("completed");
        expect(result.current.job?.status).toBe("completed");
        expect(api.getSlideJob).toHaveBeenCalledTimes(3);
    });

    it("retains the last known phase while a reconnect backs off", async () => {
        vi.mocked(api.getSlideJob)
            .mockResolvedValueOnce(job({ phase: "reviewing", stage: "reviewing" }))
            .mockRejectedValueOnce(new ApiError(503, "temporary outage"))
            .mockResolvedValueOnce(job({ phase: "revising", stage: "revising" }));
        const { result } = renderHook(() =>
            useSlideJob({ pollIntervalMs: 250, maxPollIntervalMs: 500 }),
        );

        await act(async () => {
            await result.current.startJob(payload);
            await vi.advanceTimersByTimeAsync(250);
        });
        expect(result.current.job?.phase).toBe("reviewing");

        await act(async () => {
            await vi.advanceTimersByTimeAsync(250);
        });
        expect(result.current.job?.phase).toBe("reviewing");
        expect(result.current.warning).toBe("Status temporarily unavailable—retrying.");

        await act(async () => {
            await vi.advanceTimersByTimeAsync(500);
        });
        expect(result.current.job?.phase).toBe("revising");
        expect(result.current.phaseHistory).toEqual(["queued", "reviewing", "revising"]);
    });

    it("resumes an active job from session storage after mount", async () => {
        window.sessionStorage.setItem("nhi-ai:active-slide-job-id", "job-resume");
        vi.mocked(api.getSlideJob).mockResolvedValueOnce(
            job({ job_id: "job-resume", phase: "reviewing", stage: "reviewing" }),
        );

        const { result } = renderHook(() =>
            useSlideJob({ pollIntervalMs: 250, maxPollIntervalMs: 500 }),
        );

        await act(async () => {
            await Promise.resolve();
            await Promise.resolve();
        });
        expect(result.current.job?.job_id).toBe("job-resume");
        expect(api.getSlideJob).toHaveBeenCalledWith(
            "job-resume",
            expect.objectContaining({ signal: expect.any(AbortSignal) }),
        );
        expect(result.current.phaseHistory).toContain("reviewing");
    });

    it("stops as expired for a missing job and surfaces terminal backend errors", async () => {
        vi.mocked(api.getSlideJob).mockRejectedValueOnce(new ApiError(404, "missing"));
        const { result } = renderHook(() =>
            useSlideJob({ pollIntervalMs: 250, maxPollIntervalMs: 500 }),
        );
        await act(async () => {
            await result.current.startJob(payload);
            await vi.advanceTimersByTimeAsync(250);
        });
        expect(result.current.phase).toBe("expired");
        expect(result.current.error?.message).toBe("This agent job has expired or is no longer available.");

        vi.mocked(api.createSlideJob).mockResolvedValueOnce({ job_id: "job-2", status: "queued", phase: "queued" });
        vi.mocked(api.getSlideJob).mockResolvedValueOnce(
            job({ job_id: "job-2", status: "failed", phase: "failed", stage: "failed", error: "來源格式不支援" }),
        );
        await act(async () => {
            await result.current.retry();
            await vi.advanceTimersByTimeAsync(250);
        });
        expect(result.current.phase).toBe("failed");
        expect(result.current.error?.message).toBe("來源格式不支援");
    });

    it("cancels a scheduled poll when unmounted", async () => {
        vi.mocked(api.getSlideJob).mockResolvedValue(job());
        const { result, unmount } = renderHook(() =>
            useSlideJob({ pollIntervalMs: 250, maxPollIntervalMs: 500 }),
        );
        await act(async () => {
            await result.current.startJob(payload);
        });
        unmount();
        await act(async () => {
            await vi.advanceTimersByTimeAsync(1_000);
        });
        expect(api.getSlideJob).not.toHaveBeenCalled();
    });
});
