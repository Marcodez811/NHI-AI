import type { AgentNodeSnapshot } from "../../../lib/api/agents";
import { MetaValue } from "./MetaValue";
import { formatElapsed, nodeLabel, statusLabel, statusTextClass } from "./format";
import { StatusIcon } from "./StatusIcon";

export function NodeCard({ node }: { node: AgentNodeSnapshot }) {
    const iconClass = node.status === "running"
        ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
        : node.status === "completed"
            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
            : node.status === "failed"
                ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                : "bg-secondary text-muted-foreground";

    return (
        <article className="min-w-0 rounded-lg border border-border bg-card p-4 shadow-sm">
            <div className="flex items-start gap-3">
                <span className={`mt-1 flex size-7 shrink-0 items-center justify-center rounded-full ${iconClass}`}>
                    <StatusIcon status={node.status} />
                </span>
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <h3 className="truncate text-sm font-semibold">{nodeLabel(node)}</h3>
                        <span className={`text-xs font-medium ${statusTextClass(node.status)}`}>{statusLabel(node.status)}</span>
                    </div>
                    <p className="mt-1 truncate font-mono text-[11px] text-muted-foreground">{node.node_id}</p>
                </div>
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-3 border-t border-border/70 pt-3">
                <MetaValue label="Runner" value={node.runner || "—"} />
                <MetaValue label="Model" value={node.model || "—"} />
                <MetaValue label="Reasoning" value={node.reasoning_effort || "—"} />
                <MetaValue label="最新嘗試" value={String(node.attempt ?? 0)} />
                <MetaValue label="Worker" value={node.worker_id || "—"} copyable />
                <MetaValue label="耗時" value={formatElapsed(node.duration_ms, node.started_at, node.finished_at)} />
            </dl>
            {node.message && <p className="mt-4 border-t border-border/70 pt-3 text-xs leading-5 text-muted-foreground">{node.message}</p>}
        </article>
    );
}
