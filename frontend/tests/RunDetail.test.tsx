import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { RunDetail } from "../components/dev/agent/RunDetail";
import type { AgentRunSnapshot } from "../lib/api";

const snapshot: AgentRunSnapshot = {
    run_id: "run-1",
    workflow: "slides",
    status: "running",
    phase: "reviewing",
    runner: "codex",
    task_id: "task-1",
    worker_id: "worker-1",
    started_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:01Z",
    finished_at: null,
    duration_ms: null,
    message: "Reviewing presentation",
    last_sequence: 0,
    nodes: [
        {
            node_id: "reviewer",
            agent_role: "reviewer",
            runner: "codex",
            model: "gpt-5.6-sol",
            reasoning_effort: "high",
            status: "completed",
            attempt: 3,
            task_id: "task-1",
            worker_id: "worker-1",
            provider_run_id: null,
            started_at: null,
            updated_at: "2026-01-01T00:00:01Z",
            finished_at: null,
            duration_ms: null,
            message: null,
        },
        {
            node_id: "author",
            agent_role: "author",
            runner: "codex",
            model: "gpt-5.6-luna",
            reasoning_effort: "high",
            status: "completed",
            attempt: 3,
            task_id: "task-1",
            worker_id: "worker-1",
            provider_run_id: null,
            started_at: null,
            updated_at: "2026-01-01T00:00:01Z",
            finished_at: null,
            duration_ms: null,
            message: null,
        },
        {
            node_id: "validator",
            agent_role: "validator",
            runner: "system",
            model: null,
            reasoning_effort: null,
            status: "completed",
            attempt: 3,
            task_id: "task-1",
            worker_id: "worker-1",
            provider_run_id: null,
            started_at: null,
            updated_at: "2026-01-01T00:00:01Z",
            finished_at: null,
            duration_ms: null,
            message: null,
        },
    ],
};

describe("RunDetail", () => {
    afterEach(() => cleanup());

    it("renders workflow nodes in lifecycle order and labels the latest attempt", () => {
        render(<RunDetail snapshot={snapshot} events={[]} detailLoading={false} />);

        const nodeHeadings = Array.from(document.querySelectorAll("article h3"))
            .map((heading) => heading.textContent);
        expect(nodeHeadings).toEqual(["作者 Agent", "驗證器", "審查 Agent"]);
        expect(screen.getAllByText("最新嘗試")).toHaveLength(3);
        expect(screen.queryByText("嘗試")).not.toBeInTheDocument();
        expect(screen.getByText("gpt-5.6-luna")).toBeInTheDocument();
        expect(screen.getByText("gpt-5.6-sol")).toBeInTheDocument();
        expect(screen.getAllByText("high")).toHaveLength(2);
    });
});
