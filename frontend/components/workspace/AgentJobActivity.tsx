"use client";

import {
    Check,
    CircleAlert,
    CircleDot,
    LoaderCircle,
    RefreshCw,
} from "lucide-react";
import type { AgentJobPhase } from "../../lib/api";
import type { AgentJobClientPhase, AgentJobRecord } from "../../lib/hooks/useAgentJob";

export const AGENT_JOB_PHASES: readonly AgentJobPhase[] = [
    "queued",
    "preparing",
    "drafting",
    "validating",
    "reviewing",
    "revising",
    "publishing",
    "completed",
];

export const AGENT_JOB_PHASE_LABELS: Record<AgentJobPhase, string> = {
    queued: "等待中",
    preparing: "準備中",
    drafting: "撰寫中",
    validating: "驗證中",
    reviewing: "檢查中",
    revising: "修訂中",
    publishing: "發佈中",
    completed: "完成",
    failed: "失敗",
};

const PHASE_COPY: Partial<Record<AgentJobPhase, string>> = {
    queued: "工作正在等待可用容量。",
    preparing: "正在準備來源與工作環境。",
    drafting: "正在根據來源撰寫簡報。",
    validating: "正在驗證簡報內容與格式。",
    reviewing: "正在檢查簡報是否符合需求。",
    revising: "正在根據檢查結果修訂簡報。",
    publishing: "正在整理可下載的簡報檔案。",
    completed: "簡報已完成，可以下載。",
};

function fallbackPhase(job: AgentJobRecord | null): AgentJobPhase {
    if (!job) return "queued";
    if (job.phase) return job.phase;
    if (job.status === "queued") return "queued";
    if (job.status === "completed") return "completed";
    if (job.status === "failed") return "failed";
    return "preparing";
}

function phaseMessage(job: AgentJobRecord, phase: AgentJobPhase): string {
    // Revision copy explains the intentional loop even if the worker sends a
    // generic safe message for that boundary.
    if (phase === "revising") return PHASE_COPY.revising!;
    return job.message || PHASE_COPY[phase] || "工作正在進行中。";
}

function isTerminal(job: AgentJobRecord | null): boolean {
    return job?.status === "completed" || job?.status === "failed";
}

export interface AgentJobActivityProps {
    job: AgentJobRecord | null;
    clientPhase?: AgentJobClientPhase;
    phaseHistory?: AgentJobPhase[];
    warning?: string | null;
    error?: string | null;
    workflowLabel?: string;
    onRefresh?: () => void;
    onRetry?: () => void;
}

/** Shared lifecycle view for any agentic workflow. */
export function AgentJobActivity({
    job,
    clientPhase = "polling",
    phaseHistory = [],
    warning,
    error,
    workflowLabel = "工作",
    onRefresh,
    onRetry,
}: AgentJobActivityProps) {
    if (!job) return null;

    const currentPhase = fallbackPhase(job);
    const history = new Set([...phaseHistory, currentPhase]);
    const currentIndex = AGENT_JOB_PHASES.indexOf(currentPhase);
    const active = !isTerminal(job) && (clientPhase === "polling" || clientPhase === "submitting");
    const message = phaseMessage(job, currentPhase);

    return (
        <section
            aria-labelledby="agent-job-activity-title"
            className="rounded-2xl border border-border bg-card p-5 shadow-sm"
        >
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h2 id="agent-job-activity-title" className="font-semibold">
                        {workflowLabel}工作狀態
                    </h2>
                    <p className="mt-1 text-sm text-muted-foreground">
                        代理人會在檢查後視需要回到修訂，不以百分比表示進度。
                    </p>
                </div>
                {onRefresh && !isTerminal(job) && (
                    <button
                        type="button"
                        onClick={onRefresh}
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
                    >
                        <RefreshCw size={13} />
                        重新整理
                    </button>
                )}
            </div>

            <ol className="mt-5 space-y-0" aria-label={`${workflowLabel}工作生命週期`}>
                {AGENT_JOB_PHASES.map((phaseName, index) => {
                    const isCurrent = currentPhase === phaseName;
                    const isCompleted =
                        currentPhase === "completed" ||
                        (history.has(phaseName) && !isCurrent) ||
                        (currentIndex >= 0 && index < currentIndex);
                    const isPending = !isCurrent && !isCompleted;
                    const isLast = index === AGENT_JOB_PHASES.length - 1;

                    return (
                        <li key={phaseName} className="relative flex gap-3 pb-4 last:pb-0">
                            {!isLast && (
                                <span
                                    aria-hidden="true"
                                    className={`absolute left-[9px] top-5 h-[calc(100%-0.5rem)] w-px ${isCompleted ? "bg-primary/45" : "bg-border"}`}
                                />
                            )}
                            <span
                                className={`relative z-10 flex size-5 shrink-0 items-center justify-center rounded-full border bg-card ${isCurrent ? "border-primary text-primary" : isCompleted ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground"}`}
                            >
                                {isCurrent && active ? (
                                    <LoaderCircle
                                        size={13}
                                        className="animate-spin motion-reduce:animate-none"
                                        aria-label="進行中"
                                    />
                                ) : isCompleted ? (
                                    <Check size={12} strokeWidth={2.5} />
                                ) : (
                                    <CircleDot size={10} />
                                )}
                            </span>
                            <div className="min-w-0 flex-1 -translate-y-0.5">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className={`text-sm ${isCurrent ? "font-semibold text-foreground" : isPending ? "text-muted-foreground" : "text-foreground"}`}>
                                        {AGENT_JOB_PHASE_LABELS[phaseName]}
                                    </span>
                                    {isCurrent && active && (
                                        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                                            現在
                                        </span>
                                    )}
                                </div>
                                {isCurrent && (
                                    <p
                                        role="status"
                                        aria-live="polite"
                                        className="mt-1 text-xs leading-5 text-muted-foreground"
                                    >
                                        {message}
                                    </p>
                                )}
                            </div>
                        </li>
                    );
                })}
            </ol>

            {currentPhase === "failed" && (
                <div className="mt-5 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-sm text-red-700">
                    <CircleAlert size={15} className="mt-0.5 shrink-0" />
                    <div className="min-w-0 flex-1">
                        <p className="font-medium">{AGENT_JOB_PHASE_LABELS.failed}</p>
                        <p>{error || job.error || job.message || "工作執行失敗。"}</p>
                        {onRetry && (
                            <button type="button" onClick={onRetry} className="mt-2 underline underline-offset-2">
                                再次生成
                            </button>
                        )}
                    </div>
                </div>
            )}

            {warning && (
                <div className="mt-5 flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-xs text-amber-800">
                    <CircleAlert size={14} className="mt-0.5 shrink-0" />
                    <div className="min-w-0 flex-1">
                        <p>{warning}</p>
                        {onRefresh && (
                            <button type="button" onClick={onRefresh} className="mt-1.5 font-medium underline underline-offset-2">
                                立即重試
                            </button>
                        )}
                    </div>
                </div>
            )}
        </section>
    );
}
