"use client";

import { Activity, Check, Copy } from "lucide-react";
import { useMemo, useState } from "react";
import type { AgentEvent, AgentRunSnapshot } from "../../../lib/api/agents";
import { Button } from "../../ui/button";
import { MetaValue } from "./MetaValue";
import { NodeCard } from "./NodeCard";
import {
    copyValue,
    formatElapsed,
    statusBadgeClass,
    statusLabel,
    statusTextClass,
} from "./format";
import { StatusIcon } from "./StatusIcon";
import { Timeline } from "./Timeline";
import { deriveAgentAttempts } from "./attempts";
import { useNow } from "../../../lib/hooks/useNow";

function PhaseValue({ phase }: { phase: string | null }) {
    if (phase !== "awaiting_outline") {
        return <MetaValue label="Phase" value={phase || "—"} />;
    }

    return (
        <div className="min-w-0">
            <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Phase
            </dt>
            <dd className="mt-1">
                <span className="inline-flex items-center gap-1.5 rounded-full border border-border bg-secondary px-2.5 py-1 text-xs font-medium text-muted-foreground">
                    <StatusIcon status="waiting" />
                    等待人工核准大綱
                </span>
            </dd>
        </div>
    );
}

export function RunDetail({
    snapshot,
    events,
    detailLoading,
}: {
    snapshot: AgentRunSnapshot;
    events: AgentEvent[];
    detailLoading: boolean;
}) {
    const [copied, setCopied] = useState(false);
    const now = useNow();
    const attemptsByNode = useMemo(
        () => deriveAgentAttempts(events, snapshot.nodes, now),
        [events, now, snapshot.nodes],
    );
    const nodes = useMemo(() => {
        const lifecycleOrder = [
            "extraction",
            "planning",
            "author",
            "validator",
            "reviewer",
        ];
        return snapshot.nodes
            .map((node, index) => ({ node, index }))
            .sort((left, right) => {
                const leftOrder = lifecycleOrder.indexOf(
                    left.node.node_id.toLowerCase(),
                );
                const rightOrder = lifecycleOrder.indexOf(
                    right.node.node_id.toLowerCase(),
                );
                const normalizedLeft =
                    leftOrder === -1 ? lifecycleOrder.length : leftOrder;
                const normalizedRight =
                    rightOrder === -1 ? lifecycleOrder.length : rightOrder;
                return (
                    normalizedLeft - normalizedRight || left.index - right.index
                );
            })
            .map(({ node }) => node);
    }, [snapshot.nodes]);
    const copyRunId = () => {
        copyValue(snapshot.run_id);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1_500);
    };

    return (
        <div className="space-y-6">
            <header className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <span
                            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${statusTextClass(snapshot.status)} ${statusBadgeClass(snapshot.status)}`}
                        >
                            <StatusIcon status={snapshot.status} />
                            {statusLabel(snapshot.status)}
                        </span>
                        {detailLoading && (
                            <span className="text-xs text-muted-foreground">
                                更新中…
                            </span>
                        )}
                    </div>
                    <h2 className="mt-3 truncate text-xl font-semibold tracking-tight">
                        {snapshot.workflow}
                    </h2>
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                        <span className="font-mono">{snapshot.run_id}</span>
                        <Button
                            type="button"
                            onClick={copyRunId}
                            variant="ghost"
                            size="xs"
                            aria-label="複製執行 ID"
                        >
                            {copied ? (
                                <Check size={12} aria-hidden="true" />
                            ) : (
                                <Copy size={12} aria-hidden="true" />
                            )}
                            {copied ? "已複製" : "複製"}
                        </Button>
                    </div>
                </div>
                <div className="text-left sm:text-right">
                    <p className="font-mono text-2xl font-semibold tabular-nums">
                        {formatElapsed(
                            null,
                            snapshot.started_at,
                            snapshot.finished_at,
                            now,
                        )}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">總耗時</p>
                </div>
            </header>

            {snapshot.message && (
                <div
                    className="flex items-start gap-3 rounded-lg border border-border bg-secondary/45 px-4 py-3"
                    role="status"
                    aria-live="polite"
                >
                    <Activity
                        size={16}
                        className="mt-0.5 shrink-0 text-primary"
                        aria-hidden="true"
                    />
                    <p className="text-sm leading-6">{snapshot.message}</p>
                </div>
            )}

            <dl className="grid gap-x-6 gap-y-4 rounded-lg border border-border bg-card p-4 sm:grid-cols-2 lg:grid-cols-4">
                <PhaseValue phase={snapshot.phase} />
                <MetaValue label="Runner" value={snapshot.runner || "—"} />
                <MetaValue
                    label="Task ID"
                    value={snapshot.task_id || "—"}
                    copyable
                />
                <MetaValue
                    label="Worker"
                    value={snapshot.worker_id || "—"}
                    copyable
                />
            </dl>

            <section aria-labelledby="agent-nodes-heading">
                <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                        <h3
                            id="agent-nodes-heading"
                            className="text-sm font-semibold"
                        >
                            Workflow nodes
                        </h3>
                        <p className="mt-1 text-xs text-muted-foreground">
                            邏輯節點與實際執行環境
                        </p>
                    </div>
                    <span className="text-xs text-muted-foreground">
                        {snapshot.nodes.length} 個節點
                    </span>
                </div>
                {snapshot.nodes.length ? (
                    <div className="grid gap-3 lg:grid-cols-3">
                        {nodes.map((node) => (
                            <NodeCard
                                key={node.node_id}
                                node={node}
                                attempts={attemptsByNode.get(node.node_id) ?? []}
                                now={now}
                            />
                        ))}
                    </div>
                ) : (
                    <div className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                        尚未建立節點。
                    </div>
                )}
            </section>

            <section aria-labelledby="agent-events-heading">
                <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                        <h3
                            id="agent-events-heading"
                            className="text-sm font-semibold"
                        >
                            Event timeline
                        </h3>
                        <p className="mt-1 text-xs text-muted-foreground">
                            已消毒的事件串流，依序號排列
                        </p>
                    </div>
                    <span className="text-xs text-muted-foreground">
                        {events.length} 筆事件
                    </span>
                </div>
                <Timeline events={events} />
            </section>
        </div>
    );
}
