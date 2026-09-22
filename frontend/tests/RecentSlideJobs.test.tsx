import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SlideJobSummary } from "../lib/api/slides";
import { listSlideJobs } from "../lib/api/slides";
import { RecentSlideJobs } from "../components/workspace/RecentSlideJobs";

vi.mock("../lib/api/slides", async (importOriginal) => ({
    ...await importOriginal<typeof import("../lib/api/slides")>(),
    listSlideJobs: vi.fn(),
}));

const summary = (overrides: Partial<SlideJobSummary> = {}): SlideJobSummary => ({
    job_id: "job-1",
    title: "年度政策簡報",
    status: "awaiting_input",
    phase: "awaiting_outline",
    created_at: "2026-09-21T08:00:00Z",
    started_at: "2026-09-21T08:01:00Z",
    finished_at: null,
    ...overrides,
});

describe("RecentSlideJobs", () => {
    beforeEach(() => vi.clearAllMocks());
    afterEach(() => cleanup());

    it("links parked and completed jobs to their durable pages", async () => {
        vi.mocked(listSlideJobs).mockResolvedValue([
            summary(),
            summary({ job_id: "job-2", title: "健康政策", status: "completed", phase: "completed", finished_at: "2026-09-21T09:00:00Z" }),
        ]);

        render(<RecentSlideJobs />);

        expect(await screen.findByText("年度政策簡報")).toBeInTheDocument();
        expect(screen.getByText("待審核大綱")).toBeInTheDocument();
        expect(screen.getByText("需要你確認")).toBeInTheDocument();
        expect(screen.getByText("已完成")).toBeInTheDocument();
        expect(screen.getByText("可下載")).toBeInTheDocument();
        expect(screen.getByRole("link", { name: /年度政策簡報/ })).toHaveAttribute("href", "/slides/job-1");
        expect(screen.getByRole("link", { name: /健康政策/ })).toHaveAttribute("href", "/slides/job-2");
        expect(listSlideJobs).toHaveBeenCalledWith(20, { signal: expect.any(AbortSignal) });
    });

    it("shows an empty state when there are no jobs", async () => {
        vi.mocked(listSlideJobs).mockResolvedValue([]);

        render(<RecentSlideJobs />);

        expect(await screen.findByText(/尚無簡報工作/)).toBeInTheDocument();
    });

    it("reports loaded jobs to a caller instead of fetching twice", async () => {
        const jobs = [summary(), summary({ job_id: "job-2", title: "健康政策", status: "completed", phase: "completed" })];
        vi.mocked(listSlideJobs).mockResolvedValue(jobs);
        const onJobsChange = vi.fn();

        render(<RecentSlideJobs onJobsChange={onJobsChange} />);

        expect(await screen.findByText("年度政策簡報")).toBeInTheDocument();
        expect(onJobsChange).toHaveBeenCalledWith(jobs);
        expect(listSlideJobs).toHaveBeenCalledOnce();
    });

    it("shows the failure when the list request fails", async () => {
        // Recovery is a page reload, which remounts the list and refetches it.
        vi.mocked(listSlideJobs).mockRejectedValueOnce(new Error("服務暫時無法使用"));

        render(<RecentSlideJobs />);

        expect(await screen.findByRole("alert")).toHaveTextContent("服務暫時無法使用");
    });
});
