import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentJobActivity } from "../components/workspace/AgentJobActivity";
import type { SlideJob } from "../lib/api";

const job = (phase: SlideJob["phase"]): SlideJob => ({
    job_id: "job-1",
    status: phase === "completed" ? "completed" : "running",
    phase,
    stage: phase,
    message: "safe status message",
    started_at: "2026-08-26T01:00:00Z",
    finished_at: phase === "completed" ? "2026-08-26T01:05:00Z" : null,
    error: null,
    download_url: null,
});

describe("AgentJobActivity", () => {
    afterEach(() => cleanup());

    it("shows completed work and an indeterminate current step", () => {
        render(
            <AgentJobActivity
                job={job("reviewing")}
                phaseHistory={["queued", "preparing", "drafting", "validating", "reviewing"]}
            />,
        );

        expect(screen.getByText("等待中")).toBeInTheDocument();
        expect(screen.getByText("檢查中")).toBeInTheDocument();
        expect(screen.getByText("safe status message")).toBeInTheDocument();
        expect(screen.getByText("現在")).toBeInTheDocument();
        expect(screen.queryByText(/67%/)).not.toBeInTheDocument();
    });

    it("makes the review-to-revision loop explicit", () => {
        render(
            <AgentJobActivity
                job={job("revising")}
                phaseHistory={["queued", "preparing", "drafting", "validating", "reviewing", "revising"]}
            />,
        );

        expect(screen.getByText("修訂中")).toBeInTheDocument();
        expect(screen.getByText("正在根據檢查結果修訂簡報。"))
            .toBeInTheDocument();
    });

    it("keeps a reconnect warning actionable without discarding the state", async () => {
        const onRefresh = vi.fn();
        render(
            <AgentJobActivity
                job={job("reviewing")}
                warning="Status temporarily unavailable—retrying."
                onRefresh={onRefresh}
            />,
        );

        expect(screen.getByText("Status temporarily unavailable—retrying.")).toBeInTheDocument();
        screen.getByRole("button", { name: "立即重試" }).click();
        expect(onRefresh).toHaveBeenCalledOnce();
    });

    it("renders terminal failure recovery", () => {
        render(
            <AgentJobActivity
                job={{ ...job("failed"), status: "failed", phase: "failed", error: "generation failed" }}
                error="generation failed"
                onRetry={vi.fn()}
            />,
        );

        expect(screen.getByText("失敗")).toBeInTheDocument();
        expect(screen.getByText("generation failed")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "再次生成" })).toBeInTheDocument();
    });
});
