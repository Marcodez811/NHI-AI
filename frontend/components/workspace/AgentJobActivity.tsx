"use client";

import {
    Check,
    CircleAlert,
    CircleDot,
    LoaderCircle,
    RefreshCw,
} from "lucide-react";
import { Button } from "../ui/button";
import type { AgentJobPhase } from "../../lib/api/slides";
import type {
    AgentJobClientPhase,
    AgentJobRecord,
} from "../../lib/hooks/useAgentJob";

/**
 * Canonical pipeline order used to judge "is this step before the current
 * one", independent of what this browser session happened to observe.
 * "revising" has no row of its own: every later author attempt re-enters at
 * the drafting position, so it shares that step's index.
 */
const PIPELINE_ORDER: readonly AgentJobPhase[] = [
    "queued",
    "preparing",
    "extracting",
    "planning",
    "awaiting_outline",
    "drafting",
    "validating",
    "reviewing",
    "publishing",
    "completed",
];

/** Phases the backend only emits when the corresponding feature is active. */
const CONDITIONAL_PHASES: ReadonlySet<AgentJobPhase> = new Set(["planning", "awaiting_outline"]);

const AGENT_JOB_PHASE_LABELS: Record<AgentJobPhase, string> = {
    queued: "等待中",
    preparing: "準備中",
    extracting: "整理來源中",
    planning: "規劃簡報結構中",
    awaiting_outline: "等待大綱確認",
    drafting: "撰寫中",
    validating: "驗證中",
    reviewing: "檢查中",
    revising: "修訂中",
    publishing: "發佈中",
    completed: "完成",
    failed: "失敗",
};

/**
 * Fixed, user-facing copy for every phase. The backend's raw status message
 * names its internal runner (e.g. "Codex completed a workflow work step"),
 * which is an implementation detail; this page shows only these curated
 * sentences instead, never `job.message`.
 */
const PHASE_COPY: Record<AgentJobPhase, string> = {
    queued: "工作正在等待可用容量。",
    preparing: "正在準備來源與工作環境。",
    extracting: "正在抽取來源並建立固定的證據資料。",
    planning: "正在根據來源規劃簡報結構。",
    awaiting_outline: "正在等待您檢閱與核准簡報大綱。",
    drafting: "正在依核准的大綱撰寫簡報。",
    validating: "正在檢查格式與引用。",
    reviewing: "正在核對內容與來源是否相符。",
    revising: "正在根據檢查結果修訂簡報。",
    publishing: "正在整理可下載的簡報檔案。",
    completed: "簡報已完成，可以下載。",
    failed: "工作執行失敗。",
};

function canonicalIndex(phase: AgentJobPhase): number {
    // A later author attempt occupies the drafting slot; it never gets its own row.
    const position = phase === "revising" ? "drafting" : phase;
    const index = PIPELINE_ORDER.indexOf(position);
    return index === -1 ? PIPELINE_ORDER.length : index;
}

function fallbackPhase(job: AgentJobRecord | null): AgentJobPhase {
    if (!job) return "queued";
    if (job.phase) return job.phase;
    if (job.status === "queued") return "queued";
    if (job.status === "completed") return "completed";
    if (job.status === "failed") return "failed";
    return "preparing";
}

function isTerminal(job: AgentJobRecord | null): boolean {
    return job?.status === "completed" || job?.status === "failed";
}

/**
 * How many times this session has watched the job re-enter "revising".
 * appendPhase() already collapses consecutive duplicates, so every
 * surviving "revising" entry is a distinct retry, never a re-render of the
 * same one. This intentionally has no maximum: the retry cap is a backend
 * setting this page does not know.
 */
function countRevisions(phaseHistory: AgentJobPhase[], currentPhase: AgentJobPhase): number {
    const observed = phaseHistory[phaseHistory.length - 1] === currentPhase
        ? phaseHistory
        : [...phaseHistory, currentPhase];
    return observed.filter((phase) => phase === "revising").length;
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
    const active =
        !isTerminal(job) &&
        (clientPhase === "polling" || clientPhase === "submitting");
    const revisionCount = countRevisions(phaseHistory, currentPhase);

    // Failure freezes progress mid-pipeline. Rank against the last phase this
    // session actually observed before "failed" rather than "failed" itself,
    // so steps already completed stay ticked instead of all reverting to
    // pending.
    const rankingPhase = currentPhase === "failed"
        ? [...phaseHistory].reverse().find((phase) => phase !== "failed")
        : currentPhase;
    const currentIndex = rankingPhase ? canonicalIndex(rankingPhase) : -1;

    // Mandatory steps are always shown and are correct by construction: a
    // step before the current pipeline position is done whether or not this
    // session happened to observe it. Conditional steps (planner, outline
    // review) only ever fire for some jobs, so they are omitted entirely
    // unless this session actually saw them, rather than showing a step that
    // will never complete for this job.
    const visibleSteps = PIPELINE_ORDER.filter((phaseName) => {
        if (!CONDITIONAL_PHASES.has(phaseName)) return true;
        return phaseHistory.includes(phaseName) || currentPhase === phaseName;
    });

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
                        Agent 會在檢查後視需要回到修訂，不以百分比表示進度。
                    </p>
                </div>
                {onRefresh && !isTerminal(job) && (
                    <Button
                        type="button"
                        onClick={onRefresh}
                        variant="outline"
                        size="sm"
                        className="shrink-0 gap-1.5 text-xs text-muted-foreground"
                    >
                        <RefreshCw size={13} />
                        重新整理
                    </Button>
                )}
            </div>

            <ol
                className="mt-5 space-y-0"
                aria-label={`${workflowLabel}工作生命週期`}
            >
                {visibleSteps.map((phaseName, index) => {
                    // The drafting row stands in for every later author attempt too:
                    // its label and description track whichever of the two is live.
                    const displayPhase: AgentJobPhase = phaseName === "drafting" && currentPhase === "revising"
                        ? "revising"
                        : phaseName;
                    const isCurrent = phaseName === "drafting"
                        ? currentPhase === "drafting" || currentPhase === "revising"
                        : currentPhase === phaseName;
                    const isCompleted = currentPhase === "completed" || canonicalIndex(phaseName) < currentIndex;
                    const isPending = !isCurrent && !isCompleted;
                    const isLast = index === visibleSteps.length - 1;
                    const showRevisionBadge = phaseName === "drafting" && revisionCount >= 1;

                    return (
                        <li
                            key={phaseName}
                            className="relative flex gap-3 pb-4 last:pb-0"
                        >
                            {!isLast && (
                                <span
                                    aria-hidden="true"
                                    className={`absolute left-[9px] top-5 h-[calc(100%-0.5rem)] w-px transition-colors duration-500 motion-reduce:transition-none ${isCompleted ? "bg-primary/45" : "bg-border"}`}
                                />
                            )}
                            <span
                                className={`relative z-10 flex size-5 shrink-0 items-center justify-center rounded-full border bg-card transition-colors duration-500 motion-reduce:transition-none ${isCurrent ? "border-primary text-primary" : isCompleted ? "border-primary bg-primary text-primary-foreground" : "border-border text-muted-foreground"}`}
                            >
                                {isCurrent && active ? (
                                    <LoaderCircle
                                        size={13}
                                        className="animate-spin motion-reduce:animate-none"
                                        aria-label="進行中"
                                    />
                                ) : isCompleted ? (
                                    <Check
                                        size={12}
                                        strokeWidth={2.5}
                                        className="animate-in zoom-in-50 duration-300 motion-reduce:animate-none"
                                        aria-label="已完成"
                                    />
                                ) : (
                                    <CircleDot size={10} aria-label="尚未開始" />
                                )}
                            </span>
                            <div className="min-w-0 flex-1 -translate-y-0.5">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span
                                        className={`text-sm ${isCurrent ? "font-semibold text-foreground" : isPending ? "text-muted-foreground" : "text-foreground"}`}
                                    >
                                        {AGENT_JOB_PHASE_LABELS[displayPhase]}
                                    </span>
                                    {isCurrent && active && (
                                        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
                                            現在
                                        </span>
                                    )}
                                    {showRevisionBadge && (
                                        <span
                                            key={revisionCount}
                                            className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-medium text-accent-foreground animate-in fade-in zoom-in-95 duration-300 motion-reduce:animate-none"
                                        >
                                            第 {revisionCount} 次修訂
                                        </span>
                                    )}
                                </div>
                                {isCurrent && (
                                    <p
                                        key={displayPhase}
                                        role="status"
                                        aria-live="polite"
                                        className="mt-1 text-xs leading-5 text-muted-foreground animate-in fade-in duration-300 motion-reduce:animate-none"
                                    >
                                        {PHASE_COPY[displayPhase]}
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
                        <p className="font-medium">
                            {AGENT_JOB_PHASE_LABELS.failed}
                        </p>
                        <p>
                            {error || job.error || PHASE_COPY.failed}
                        </p>
                        {onRetry && (
                            <Button
                                type="button"
                                onClick={onRetry}
                                variant="link"
                                size="sm"
                                className="mt-2 h-auto p-0"
                            >
                                調整設定後重試
                            </Button>
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
                            <Button
                                type="button"
                                onClick={onRefresh}
                                variant="link"
                                size="sm"
                                className="mt-1.5 h-auto p-0 font-medium"
                            >
                                立即重試
                            </Button>
                        )}
                    </div>
                </div>
            )}
        </section>
    );
}
