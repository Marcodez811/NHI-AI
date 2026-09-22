import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SlideJob } from "../lib/api/slides";
import { useSlideJob } from "../lib/hooks/useSlideJob";
import { SlideJobView } from "../components/workspace/SlideJobView";

const navigation = vi.hoisted(() => ({ push: vi.fn() }));
const outlineRender = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("../lib/hooks/useSlideJob", () => ({ useSlideJob: vi.fn() }));
vi.mock("../components/workspace/OutlineReview", () => ({
    OutlineReview: ({ jobId, onApproved }: { jobId: string; onApproved: () => void }) => {
        outlineRender(jobId);
        return <button type="button" onClick={onApproved}>核准大綱</button>;
    },
}));

const job = (overrides: Partial<SlideJob> = {}): SlideJob => ({
    job_id: "job-route",
    status: "running",
    phase: "extracting",
    stage: "extracting",
    message: "正在整理來源",
    started_at: null,
    finished_at: null,
    error: null,
    download_url: null,
    brief: {
        title: "年度政策簡報",
        document_ids: [],
        slides_count: 8,
        guidance: "",
        tone: "formal",
    },
    ...overrides,
});

const resumePolling = vi.fn();
const pollNow = vi.fn(async () => undefined);

function showJob(currentJob: SlideJob | null, phase: "idle" | "polling" | "awaiting_outline" | "completed" | "failed" | "expired") {
    vi.mocked(useSlideJob).mockReturnValue({
        job: currentJob,
        phase,
        phaseHistory: currentJob ? [currentJob.phase] : [],
        submitting: false,
        polling: phase === "polling",
        error: null,
        warning: null,
        consecutivePollFailures: 0,
        startJob: vi.fn(),
        start: vi.fn(),
        pollNow,
        resumePolling,
        retry: vi.fn(),
        reset: vi.fn(),
    });
}

describe("SlideJobView", () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    afterEach(() => cleanup());

    it.each(["extracting", "planning", "drafting"] as const)(
        "shows progress for the %s lifecycle phase on the route-owned job",
        (jobPhase) => {
            showJob(job({ phase: jobPhase, stage: jobPhase }), "polling");

            render(<SlideJobView jobId="job-route" docs={[]} />);

            expect(useSlideJob).toHaveBeenCalledWith({ jobId: "job-route" });
            expect(screen.getByRole("heading", { name: "年度政策簡報" })).toBeInTheDocument();
            expect(screen.getByRole("heading", { name: "簡報工作狀態" })).toBeInTheDocument();
        },
    );

    it("reuses OutlineReview while parked and resumes polling on approval", async () => {
        showJob(job({ status: "awaiting_input", phase: "awaiting_outline" }), "awaiting_outline");
        const user = userEvent.setup();

        render(<SlideJobView jobId="job-route" docs={[]} />);

        expect(outlineRender).toHaveBeenCalledWith("job-route");
        expect(screen.getByText("請檢閱大綱並核准，簡報才會繼續生成。")).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "核准大綱" }));
        expect(resumePolling).toHaveBeenCalledOnce();
    });

    it("offers the published download", () => {
        showJob(job({ status: "completed", phase: "completed", download_url: "/download/job-route" }), "completed");

        render(<SlideJobView jobId="job-route" docs={[]} />);

        expect(screen.getByRole("link", { name: "下載 PPTX" })).toHaveAttribute("href", "/download/job-route");
    });

    it("shows a clear failed state", () => {
        showJob(job({ status: "failed", phase: "failed", error: "來源格式不支援" }), "failed");

        render(<SlideJobView jobId="job-route" docs={[]} />);

        expect(screen.getByText("來源格式不支援")).toBeInTheDocument();
        expect(screen.getByRole("link", { name: "返回簡報列表" })).toHaveAttribute("href", "/slides");
    });

    it("renders not-found for an unknown or deleted id", () => {
        showJob(null, "expired");

        render(<SlideJobView jobId="job-missing" docs={[]} />);

        expect(screen.getByRole("heading", { name: "找不到簡報工作" })).toBeInTheDocument();
        expect(screen.queryByText("正在載入簡報工作…")).not.toBeInTheDocument();
    });
});
