import type { AgentNodeSnapshot } from "../../../lib/api/agents";

const RUN_STATUS_LABELS: Record<string, string> = {
    queued: "排隊中",
    running: "執行中",
    completed: "完成",
    failed: "失敗",
};

const NODE_STATUS_LABELS: Record<string, string> = {
    pending: "等待中",
    waiting: "等待中",
    running: "執行中",
    completed: "完成",
    failed: "失敗",
};

const EVENT_LABELS: Record<string, string> = {
    run_started: "工作開始",
    phase_changed: "階段變更",
    node_started: "節點開始",
    node_progress: "節點進度",
    node_completed: "節點完成",
    node_failed: "節點失敗",
    heartbeat: "心跳",
    run_completed: "工作完成",
    run_failed: "工作失敗",
};

export function statusLabel(status: string | null | undefined): string {
    return RUN_STATUS_LABELS[status || ""] ?? NODE_STATUS_LABELS[status || ""] ?? (status || "未知");
}

export function statusTextClass(status: string | null | undefined): string {
    if (status === "completed") return "text-emerald-700 dark:text-emerald-400";
    if (status === "failed") return "text-red-700 dark:text-red-400";
    if (status === "running") return "text-blue-700 dark:text-blue-400";
    return "text-muted-foreground";
}

export function statusDotClass(status: string | null | undefined): string {
    if (status === "completed") return "bg-emerald-500";
    if (status === "failed") return "bg-red-500";
    if (status === "running") return "bg-blue-500";
    return "bg-muted-foreground/50";
}

export function statusBadgeClass(status: string | null | undefined): string {
    if (status === "failed") return "border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/40";
    if (status === "completed") return "border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/40";
    return "border-blue-200 bg-blue-50 dark:border-blue-900 dark:bg-blue-950/40";
}

export function nodeLabel(node: Pick<AgentNodeSnapshot, "node_id">): string {
    const key = node.node_id.toLowerCase();
    if (key.includes("author")) return "作者 Agent";
    if (key.includes("review")) return "審查 Agent";
    if (key.includes("valid")) return "驗證器";
    return node.node_id;
}

export function eventLabel(eventType: string): string {
    return EVENT_LABELS[eventType] ?? eventType.replaceAll("_", " ");
}

export function formatDateTime(value?: string | null): string {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return value;
    return new Intl.DateTimeFormat("zh-TW", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
    }).format(date);
}

export function formatElapsed(durationMs?: number | null, startedAt?: string | null, finishedAt?: string | null): string {
    let value = durationMs ?? null;
    if (value === null && startedAt) {
        const end = finishedAt ? new Date(finishedAt).valueOf() : Date.now();
        const start = new Date(startedAt).valueOf();
        if (!Number.isNaN(start) && !Number.isNaN(end)) value = Math.max(0, end - start);
    }
    if (value === null || !Number.isFinite(value)) return "—";
    const seconds = Math.floor(value / 1000);
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    return `${minutes}m ${String(seconds % 60).padStart(2, "0")}s`;
}

export function compactId(value: string | null | undefined): string {
    if (!value) return "—";
    if (value.length <= 18) return value;
    return `${value.slice(0, 8)}…${value.slice(-6)}`;
}

export function copyValue(value: string | null | undefined): void {
    if (value && typeof navigator !== "undefined" && navigator.clipboard) {
        void navigator.clipboard.writeText(value);
    }
}
