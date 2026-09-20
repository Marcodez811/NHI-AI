import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../lib/api";
import { OutlineReview } from "../components/workspace/OutlineReview";

const getOutline = vi.hoisted(() => vi.fn());
const approveOutline = vi.hoisted(() => vi.fn());
const streamOutlineMessage = vi.hoisted(() => vi.fn());

vi.mock("../lib/api", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../lib/api")>()),
    getSlideJobOutline: getOutline,
    approveSlideJobOutline: approveOutline,
    streamSlideJobOutlineMessage: streamOutlineMessage,
}));

const revision = (number: number) => ({
    job_id: "job-1",
    revision: number,
    session_id: "session-1",
    created_at: "2026-09-20T00:00:00Z",
    approved_at: null,
    outline: {
        title: "健保政策簡報",
        narrative: "由政策背景說明進入改革影響。",
        total_slides: 8,
        nodes: [{
            id: "impact",
            heading: "改革影響",
            intent: "說明改革帶來的改變。",
            key_points: ["民眾影響", "執行重點"],
            emphasis: "normal" as const,
            approx_slides: 2,
        }],
    },
});

describe("OutlineReview", () => {
    afterEach(() => cleanup());

    beforeEach(() => {
        getOutline.mockReset().mockResolvedValue(revision(2));
        approveOutline.mockReset().mockResolvedValue({ job_id: "job-1", status: "awaiting_input", phase: "awaiting_outline", approved_revision: 2 });
        streamOutlineMessage.mockReset();
    });

    it("approves exactly the revision displayed to the analyst", async () => {
        const user = userEvent.setup();
        render(<OutlineReview jobId="job-1" onApproved={vi.fn()} />);
        await user.click(await screen.findByRole("button", { name: "核准第 2 版" }));
        expect(approveOutline).toHaveBeenCalledWith("job-1", 2);
    });

    it("reloads and explains a stale approval without approving the newer revision", async () => {
        const user = userEvent.setup();
        approveOutline.mockRejectedValueOnce(new ApiError(409, "版本已過期"));
        getOutline.mockResolvedValueOnce(revision(2)).mockResolvedValueOnce(revision(3));
        render(<OutlineReview jobId="job-1" onApproved={vi.fn()} />);
        await screen.findByRole("button", { name: "核准第 2 版" });
        await user.click(screen.getByRole("button", { name: "核准第 2 版" }));
        expect(await screen.findByText("大綱已有新版本，已重新載入；請檢閱後再核准。系統未自動核准新版。")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "核准第 3 版" })).toBeInTheDocument();
        expect(approveOutline).toHaveBeenCalledTimes(1);
    });

    it("resumes status polling when a refreshed outline is already approved", async () => {
        const onApproved = vi.fn();
        getOutline.mockResolvedValueOnce({
            ...revision(2),
            approved_at: "2026-09-20T00:05:00Z",
        });

        render(<OutlineReview jobId="job-1" onApproved={onApproved} />);

        expect(await screen.findByText("第 2 版 · 核准後才會開始撰寫簡報。")).toBeInTheDocument();
        await waitFor(() => expect(onApproved).toHaveBeenCalledTimes(1));
    });
});
