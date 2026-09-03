import type { AgentNodeSnapshot } from "../../../lib/api/agents";
import type { AgentAttemptView } from "./attempts";
import { MetaValue } from "./MetaValue";
import { formatElapsed, nodeLabel, statusLabel, statusTextClass } from "./format";
import { StatusIcon } from "./StatusIcon";

function heartbeatLabel(value: string | null, now: number): string {
    if (!value) return "尚未收到";
    const age = Math.max(0, now - new Date(value).valueOf());
    if (!Number.isFinite(age)) return "時間無效";
    if (age > 30_000) return "30 秒以上未活動";
    return `${Math.floor(age / 1_000)} 秒前`;
}

export function NodeCard({
    node,
    attempts,
    now,
}: {
    node: AgentNodeSnapshot;
    attempts: AgentAttemptView[];
    now: number;
}) {
    const iconClass = node.status === "running"
        ? "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
        : node.status === "completed"
            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
            : node.status === "failed"
                ? "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300"
                : "bg-secondary text-muted-foreground";

    const currentAttempt = attempts.find((item) => item.attempt === node.attempt) ?? attempts.at(-1);
    const cumulativeMs = attempts.reduce((total, item) => total + (item.durationMs ?? 0), 0);
    const completedAttempts = attempts.filter((item) => item.status === "completed" || item.status === "failed").length;

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
                <MetaValue label="目前嘗試" value={String(currentAttempt?.attempt ?? node.attempt ?? "—")} />
                <MetaValue label="Worker" value={node.worker_id || "—"} copyable />
                <MetaValue label="本次耗時" value={formatElapsed(currentAttempt?.durationMs, currentAttempt?.startedAt, currentAttempt?.finishedAt, now)} />
                <MetaValue label="累計耗時" value={formatElapsed(cumulativeMs)} />
                <MetaValue label="完成嘗試" value={`${completedAttempts}/${attempts.length}`} />
                <MetaValue label="最後心跳" value={heartbeatLabel(node.last_heartbeat_at, now)} />
            </dl>
            {attempts.length > 1 && (
                <ol className="mt-4 space-y-1 border-t border-border/70 pt-3 text-xs text-muted-foreground">
                    {attempts.map((attempt) => (
                        <li key={attempt.attempt} className="flex items-center justify-between gap-3">
                            <span>嘗試 {attempt.attempt} · {statusLabel(attempt.status)}</span>
                            <span className="font-mono tabular-nums">{formatElapsed(attempt.durationMs, attempt.startedAt, attempt.finishedAt, now)}</span>
                        </li>
                    ))}
                </ol>
            )}
            {node.message && <p className="mt-4 border-t border-border/70 pt-3 text-xs leading-5 text-muted-foreground">{node.message}</p>}
        </article>
    );
}
