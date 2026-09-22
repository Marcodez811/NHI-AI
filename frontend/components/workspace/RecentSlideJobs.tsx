"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, CircleAlert, RefreshCw } from "lucide-react";

import { listSlideJobs } from "../../lib/api/slides";
import type { AgentJobPhase, SlideJobSummary } from "../../lib/api/slides";
import { Button } from "../ui/button";
import { formatDateTime } from "./WorkspaceViewUtils";

const PHASE_LABELS: Record<AgentJobPhase, string> = {
    queued: "等待中",
    preparing: "準備中",
    extracting: "整理來源中",
    planning: "規劃大綱中",
    awaiting_outline: "等待大綱確認",
    drafting: "撰寫中",
    validating: "驗證中",
    reviewing: "檢查中",
    revising: "修訂中",
    publishing: "發佈中",
    completed: "已完成",
    failed: "失敗",
};

function jobState(job: SlideJobSummary) {
    if (job.status === "awaiting_input" && job.phase === "awaiting_outline") {
        return {
            label: "待審核大綱",
            detail: "需要你確認",
            classes: "bg-amber-100 text-amber-900 dark:bg-amber-900/30 dark:text-amber-200",
            rowClasses: "bg-amber-50/70 dark:bg-amber-950/20",
        };
    }
    if (job.status === "completed") {
        return {
            label: "已完成",
            detail: "可下載",
            classes: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/30 dark:text-emerald-200",
            rowClasses: "",
        };
    }
    if (job.status === "failed") {
        return {
            label: "失敗",
            detail: "查看原因",
            classes: "bg-red-100 text-red-900 dark:bg-red-900/30 dark:text-red-200",
            rowClasses: "",
        };
    }
    return {
        label: job.status === "queued" ? "排隊中" : "執行中",
        detail: PHASE_LABELS[job.phase],
        classes: "bg-secondary text-foreground",
        rowClasses: "",
    };
}

/** The index reads fresh server state; a browser tab is not a job registry. */
export function RecentSlideJobs({
    onJobsChange,
}: {
    /** Lets the index tab badge reuse this fetch instead of loading the list twice. */
    onJobsChange?: (jobs: SlideJobSummary[] | null) => void;
} = {}) {
    const [jobs, setJobs] = useState<SlideJobSummary[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);

    useEffect(() => {
        const controller = new AbortController();
        setError(null);
        void listSlideJobs(20, { signal: controller.signal })
            .then(setJobs)
            .catch((cause: unknown) => {
                if (!controller.signal.aborted) {
                    setError(cause instanceof Error ? cause.message : "無法載入簡報工作，請稍後重試。");
                }
            });
        return () => controller.abort();
    }, [reload]);

    useEffect(() => {
        onJobsChange?.(jobs);
    }, [jobs, onJobsChange]);

    return (
        <section aria-labelledby="recent-slide-jobs-title" className="mt-12 border-t border-border pt-8">
            <div className="flex items-start justify-between gap-4">
                <div>
                    <h2 id="recent-slide-jobs-title" className="text-lg font-semibold tracking-tight">最近的簡報工作</h2>
                    <p className="mt-1 text-sm text-muted-foreground">從這裡返回待審核的大綱，或開啟已完成的簡報。</p>
                </div>
                <Button type="button" variant="ghost" size="sm" onClick={() => setReload((value) => value + 1)} aria-label="重新整理簡報工作" className="shrink-0 gap-1.5">
                    <RefreshCw size={14} aria-hidden="true" />
                    重新整理
                </Button>
            </div>

            {error ? (
                <div role="alert" className="mt-5 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    <CircleAlert size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
                    <span>無法載入簡報工作：{error}</span>
                </div>
            ) : jobs === null ? (
                <p role="status" className="mt-5 py-5 text-sm text-muted-foreground">正在載入簡報工作…</p>
            ) : jobs.length === 0 ? (
                <p className="mt-5 rounded-lg border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">尚無簡報工作。建立第一份簡報後，便能在此返回查看進度。</p>
            ) : (
                <ul className="mt-5 divide-y divide-border border-y border-border">
                    {jobs.map((job) => {
                        const state = jobState(job);
                        return (
                            <li key={job.job_id} className={state.rowClasses}>
                                <Link href={`/slides/${encodeURIComponent(job.job_id)}`} className="group flex items-center gap-4 px-2 py-4 transition-colors hover:bg-accent/60 focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-2">
                                    <span className="min-w-0 flex-1">
                                        <span className="block truncate text-sm font-semibold group-hover:text-primary">{job.title}</span>
                                        <span className="mt-1 block text-xs text-muted-foreground">
                                            {PHASE_LABELS[job.phase]} · <time dateTime={job.created_at}>{formatDateTime(job.created_at)}</time>
                                        </span>
                                    </span>
                                    <span className="flex shrink-0 flex-col items-end gap-1">
                                        <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${state.classes}`}>{state.label}</span>
                                        <span className="text-xs text-muted-foreground">{state.detail}</span>
                                    </span>
                                    <ArrowUpRight size={16} className="shrink-0 text-muted-foreground group-hover:text-primary" aria-hidden="true" />
                                </Link>
                            </li>
                        );
                    })}
                </ul>
            )}
        </section>
    );
}
