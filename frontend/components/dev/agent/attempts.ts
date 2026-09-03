import type { AgentEvent, AgentNodeSnapshot } from "../../../lib/api/agents";

export interface AgentAttemptView {
    nodeId: string;
    attempt: number;
    status: string;
    startedAt: string | null;
    finishedAt: string | null;
    durationMs: number | null;
}

const NODE_LIFECYCLE_EVENTS = new Set(["node_started", "node_completed", "node_failed"]);

function elapsed(startedAt: string | null, finishedAt: string | null, now: number): number | null {
    if (!startedAt) return null;
    const start = new Date(startedAt).valueOf();
    const end = finishedAt ? new Date(finishedAt).valueOf() : now;
    return Number.isNaN(start) || Number.isNaN(end) ? null : Math.max(0, end - start);
}

/** Reconstruct activation history from lifecycle events; snapshots hold latest only. */
export function deriveAgentAttempts(
    events: AgentEvent[],
    nodes: AgentNodeSnapshot[],
    now = Date.now(),
): Map<string, AgentAttemptView[]> {
    const byKey = new Map<string, AgentAttemptView>();
    const keyFor = (nodeId: string, attempt: number) => `${nodeId}:${attempt}`;

    for (const event of events) {
        if (!event.node_id || event.attempt === null || !NODE_LIFECYCLE_EVENTS.has(event.event_type)) continue;
        const key = keyFor(event.node_id, event.attempt);
        const current = byKey.get(key) ?? {
            nodeId: event.node_id,
            attempt: event.attempt,
            status: "running",
            startedAt: null,
            finishedAt: null,
            durationMs: null,
        };
        if (event.event_type === "node_started") {
            current.status = "running";
            current.startedAt = current.startedAt ?? event.occurred_at;
        } else {
            current.status = event.event_type === "node_completed" ? "completed" : "failed";
            current.finishedAt = event.occurred_at;
            current.durationMs = event.duration_ms;
        }
        byKey.set(key, current);
    }

    for (const node of nodes) {
        if (node.attempt === null) continue;
        const key = keyFor(node.node_id, node.attempt);
        const current = byKey.get(key);
        const snapshotAttempt: AgentAttemptView = {
            nodeId: node.node_id,
            attempt: node.attempt,
            status: node.status,
            startedAt: node.started_at,
            finishedAt: node.finished_at,
            durationMs: node.duration_ms,
        };
        byKey.set(key, current ? {
            ...current,
            status: snapshotAttempt.status || current.status,
            startedAt: current.startedAt ?? snapshotAttempt.startedAt,
            finishedAt: snapshotAttempt.finishedAt ?? current.finishedAt,
            durationMs: snapshotAttempt.durationMs ?? current.durationMs,
        } : snapshotAttempt);
    }

    const byNode = new Map<string, AgentAttemptView[]>();
    for (const item of byKey.values()) {
        const durationMs = elapsed(item.startedAt, item.finishedAt, now) ?? item.durationMs;
        const list = byNode.get(item.nodeId) ?? [];
        list.push({ ...item, durationMs });
        byNode.set(item.nodeId, list);
    }
    for (const list of byNode.values()) list.sort((left, right) => left.attempt - right.attempt);
    return byNode;
}
