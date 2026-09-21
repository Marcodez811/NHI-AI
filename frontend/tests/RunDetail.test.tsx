import { cleanup, render, screen, within } from "@testing-library/react";
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
    last_heartbeat_at: null,
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
            last_heartbeat_at: null,
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
            last_heartbeat_at: null,
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
            last_heartbeat_at: null,
        },
    ],
};

describe("RunDetail", () => {
    afterEach(() => cleanup());

    it("renders workflow nodes in lifecycle order and labels their reconstructed attempts", () => {
        render(<RunDetail snapshot={snapshot} events={[]} detailLoading={false} />);

        const nodeHeadings = Array.from(document.querySelectorAll("article h3"))
            .map((heading) => heading.textContent);
        expect(nodeHeadings).toEqual(["作者 Agent", "驗證器", "審查 Agent"]);
        expect(screen.getAllByText("目前嘗試")).toHaveLength(3);
        expect(screen.getAllByText("3")).toHaveLength(3);
        expect(screen.getByText("gpt-5.6-luna")).toBeInTheDocument();
        expect(screen.getByText("gpt-5.6-sol")).toBeInTheDocument();
        expect(screen.getAllByText("high")).toHaveLength(2);
    });

    it("shows runner configuration on each node and placeholders for missing values", () => {
        render(<RunDetail snapshot={snapshot} events={[]} detailLoading={false} />);

        const authorCard = screen.getByRole("heading", { name: "作者 Agent" })
            .closest("article") as HTMLElement;
        const validatorCard = screen.getByRole("heading", { name: "驗證器" })
            .closest("article") as HTMLElement;
        const valueFor = (card: HTMLElement, label: string) =>
            within(card).getByText(label).nextElementSibling;

        expect(valueFor(authorCard, "Runner")).toHaveTextContent("codex");
        expect(valueFor(authorCard, "Model")).toHaveTextContent("gpt-5.6-luna");
        expect(valueFor(authorCard, "Reasoning")).toHaveTextContent("high");
        expect(valueFor(validatorCard, "Runner")).toHaveTextContent("system");
        expect(valueFor(validatorCard, "Model")).toHaveTextContent("—");
        expect(valueFor(validatorCard, "Reasoning")).toHaveTextContent("—");
    });

    it("orders extraction and planning before execution nodes while preserving unknown nodes", () => {
        const nodeById = Object.fromEntries(
            snapshot.nodes.map((node) => [node.node_id, node]),
        );
        const expandedSnapshot: AgentRunSnapshot = {
            ...snapshot,
            nodes: [
                { ...nodeById.reviewer, node_id: "future-node" },
                nodeById.reviewer,
                { ...nodeById.author, node_id: "planning" },
                nodeById.validator,
                { ...nodeById.author, node_id: "extraction" },
                nodeById.author,
            ],
        };

        render(
            <RunDetail
                snapshot={expandedSnapshot}
                events={[]}
                detailLoading={false}
            />,
        );

        const nodeHeadings = Array.from(document.querySelectorAll("article h3"))
            .map((heading) => heading.textContent);
        expect(nodeHeadings).toEqual([
            "擷取 Agent",
            "規劃 Agent",
            "作者 Agent",
            "驗證器",
            "審查 Agent",
            "future-node",
        ]);
    });

    it("presents awaiting outline as waiting for human approval", () => {
        render(
            <RunDetail
                snapshot={{ ...snapshot, phase: "awaiting_outline" }}
                events={[]}
                detailLoading={false}
            />,
        );

        expect(screen.getByText("等待人工核准大綱")).toHaveClass(
            "bg-secondary",
            "text-muted-foreground",
        );
    });
});
