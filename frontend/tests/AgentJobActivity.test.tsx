import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AgentJobActivity } from "../components/workspace/AgentJobActivity";
import type { SlideJob } from "../lib/api";

const RAW_BACKEND_MESSAGE = "Codex completed a workflow work step";

const job = (phase: SlideJob["phase"], overrides: Partial<SlideJob> = {}): SlideJob => ({
    job_id: "job-1",
    status: phase === "completed" ? "completed" : phase === "failed" ? "failed" : "running",
    phase,
    stage: phase,
    message: RAW_BACKEND_MESSAGE,
    started_at: "2026-08-26T01:00:00Z",
    finished_at: phase === "completed" ? "2026-08-26T01:05:00Z" : null,
    error: null,
    download_url: null,
    ...overrides,
});

describe("AgentJobActivity", () => {
    afterEach(() => cleanup());

    it("never renders the backend's raw status message, only fixed Chinese copy", () => {
        render(
            <AgentJobActivity
                job={job("reviewing")}
                phaseHistory={["queued", "preparing", "extracting", "drafting", "validating", "reviewing"]}
            />,
        );

        expect(screen.queryByText(RAW_BACKEND_MESSAGE)).not.toBeInTheDocument();
        expect(screen.getByText("正在核對內容與來源是否相符。")).toBeInTheDocument();
        expect(screen.getByText("現在")).toBeInTheDocument();
    });

    it("ticks every mandatory step before the current one even when this session never observed it", () => {
        // A fast job can reach "reviewing" without this browser ever having
        // polled through "extracting" or "validating"; ticks must come from
        // pipeline position, not from what happened to be observed.
        render(<AgentJobActivity job={job("reviewing")} phaseHistory={[]} />);

        for (const label of ["等待中", "準備中", "整理來源中", "撰寫中", "驗證中"]) {
            const item = screen.getByText(label).closest("li")!;
            expect(within(item).getByLabelText("已完成")).toBeInTheDocument();
        }
        const current = screen.getByText("檢查中").closest("li")!;
        expect(within(current).getByLabelText("進行中")).toBeInTheDocument();
    });

    it("omits the planner and outline steps when this job's session never observed them", () => {
        render(
            <AgentJobActivity
                job={job("drafting")}
                phaseHistory={["queued", "preparing", "extracting", "drafting"]}
            />,
        );

        expect(screen.queryByText("規劃簡報結構中")).not.toBeInTheDocument();
        expect(screen.queryByText("等待大綱確認")).not.toBeInTheDocument();
    });

    it("shows the planner and outline steps, ticked, once this session observed them", () => {
        render(
            <AgentJobActivity
                job={job("drafting")}
                phaseHistory={["queued", "preparing", "extracting", "planning", "awaiting_outline", "drafting"]}
            />,
        );

        const planning = screen.getByText("規劃簡報結構中").closest("li")!;
        const outline = screen.getByText("等待大綱確認").closest("li")!;
        expect(within(planning).getByLabelText("已完成")).toBeInTheDocument();
        expect(within(outline).getByLabelText("已完成")).toBeInTheDocument();
    });

    it("folds a revision attempt into the drafting row instead of a standalone step", () => {
        render(
            <AgentJobActivity
                job={job("revising")}
                phaseHistory={["queued", "preparing", "extracting", "drafting", "validating", "reviewing", "revising"]}
            />,
        );

        // Still exactly one row for the author step, now labelled for the retry.
        expect(screen.getAllByText("修訂中")).toHaveLength(1);
        expect(screen.getByText("正在根據檢查結果修訂簡報。")).toBeInTheDocument();
        expect(screen.getByText("第 1 次修訂")).toBeInTheDocument();
    });

    it("counts each distinct entry into revising and never shows a maximum", () => {
        render(
            <AgentJobActivity
                job={job("revising")}
                phaseHistory={[
                    "queued", "preparing", "extracting", "drafting", "validating", "reviewing",
                    "revising", "validating", "reviewing",
                    "revising",
                ]}
            />,
        );

        expect(screen.getByText("第 2 次修訂")).toBeInTheDocument();
        expect(screen.queryByText(/\d\s*\/\s*\d/)).not.toBeInTheDocument();
        expect(screen.queryByText(/上限|最多次數|最多重試/)).not.toBeInTheDocument();
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

    it("renders terminal failure recovery without ever falling back to the raw backend message", () => {
        render(
            <AgentJobActivity
                job={job("failed", { error: null })}
                error="generation failed"
                onRetry={vi.fn()}
            />,
        );

        expect(screen.getByText("失敗")).toBeInTheDocument();
        expect(screen.getByText("generation failed")).toBeInTheDocument();
        expect(screen.queryByText(RAW_BACKEND_MESSAGE)).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: "調整設定後重試" })).toBeInTheDocument();
    });
});
