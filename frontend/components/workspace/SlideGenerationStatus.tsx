"use client";

import { CheckCircle2, CircleAlert, Sparkles } from "lucide-react";

import type { AgentJobPhase, SlideJob } from "../../lib/api/slides";
import type { AgentJobClientPhase } from "../../lib/hooks/useAgentJob";
import { Button } from "../ui/button";
import { AgentJobActivity } from "./AgentJobActivity";

export function SlideGenerationStatus({
    job,
    phase,
    phaseHistory,
    error,
    warning,
    pollNow,
    retry,
    canGenerate,
    readinessMessage,
    selectedCount,
    notReadyCount,
    unsupportedCount,
    start,
}: {
    job: SlideJob | null;
    phase: AgentJobClientPhase;
    phaseHistory: AgentJobPhase[];
    error: string | null;
    warning: string | null;
    pollNow?: () => Promise<SlideJob | undefined>;
    retry: () => void;
    canGenerate: boolean;
    readinessMessage: string;
    selectedCount: number;
    notReadyCount: number;
    unsupportedCount: number;
    start: () => Promise<void>;
}) {
    const busy = phase === "submitting" || phase === "polling";
    const terminal = job?.status === "failed" || job?.status === "completed";

    return (
        <>
            {error && !job && (
                <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-xs text-red-700">
                    <CircleAlert size={14} />
                    {error}
                </div>
            )}
            {warning && !job && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-xs text-amber-800">
                    <CircleAlert size={14} className="mt-0.5 shrink-0" />
                    <div className="min-w-0 flex-1">
                        <p>{warning}</p>
                        {pollNow && (
                            <Button
                                type="button"
                                onClick={() => void pollNow()}
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
            {job && (busy || terminal) && (
                <AgentJobActivity
                    job={job}
                    clientPhase={phase}
                    phaseHistory={phaseHistory}
                    warning={warning}
                    error={error}
                    workflowLabel="簡報"
                    onRefresh={pollNow ? () => void pollNow() : undefined}
                    onRetry={retry}
                />
            )}
            {phase === "expired" && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                    簡報工作已過期或不存在，請重新生成。
                </div>
            )}
            <div
                aria-live="polite"
                className={canGenerate
                    ? "flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-3 text-sm text-emerald-800"
                    : "flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800"}
            >
                {canGenerate
                    ? <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
                    : <CircleAlert size={16} className="mt-0.5 shrink-0" />}
                <span>{readinessMessage}</span>
            </div>
            <Button
                type="button"
                onClick={() => void start()}
                aria-label="生成簡報"
                disabled={!canGenerate}
                variant={canGenerate ? "default" : "secondary"}
                className={canGenerate ? "w-full gap-2 py-2.5" : "w-full cursor-not-allowed gap-2 py-2.5"}
            >
                <Sparkles size={16} />
                {phase === "submitting" ? "送出中…" : "開始執行"}
            </Button>
            {selectedCount > 0 && (notReadyCount > 0 || unsupportedCount > 0) && (
                <p className="text-xs text-muted-foreground">
                    {notReadyCount ? `${notReadyCount} 份文件尚未就緒，完成索引後才能生成` : ""}
                    {unsupportedCount
                        ? `${notReadyCount ? "；" : ""}${unsupportedCount} 份文件格式不支援，請先移除`
                        : ""}
                    。
                </p>
            )}
        </>
    );
}
