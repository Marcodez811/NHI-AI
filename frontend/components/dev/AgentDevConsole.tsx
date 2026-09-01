"use client";

import { AlertTriangle, Clock3, RefreshCw, TerminalSquare } from "lucide-react";
import { useMemo } from "react";
import { useAgentDevRuns } from "../../lib/hooks/useAgentDevRuns";
import { Button } from "../ui/button";
import { compactId, formatDateTime } from "./agent/format";
import { RunDetail } from "./agent/RunDetail";
import { RunList } from "./agent/RunList";

export function AgentDevConsole() {
    const telemetry = useAgentDevRuns();
    const selectedSummary = useMemo(
        () => telemetry.runs.find((run) => run.run_id === telemetry.selectedRunId),
        [telemetry.runs, telemetry.selectedRunId],
    );

    const selectRun = (runId: string) => {
        telemetry.selectRun(runId);
        void telemetry.refresh();
    };

    return (
        <main className="min-h-screen bg-background text-foreground">
            <div className="mx-auto max-w-[1500px] px-4 py-5 sm:px-6 lg:px-8 lg:py-8">
                <header className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-border pb-5">
                    <div>
                        <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.16em] text-primary"><TerminalSquare size={14} aria-hidden="true" /> Developer telemetry</div>
                        <h1 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">Agent 執行追蹤</h1>
                        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">檢視工作流程節點、執行環境與已消毒的事件時間軸。</p>
                    </div>
                    <div className="flex items-center gap-3">
                        {telemetry.lastUpdatedAt && <span className="hidden text-xs text-muted-foreground sm:inline">最後更新 {formatDateTime(telemetry.lastUpdatedAt.toISOString())}</span>}
                        <Button type="button" onClick={() => void telemetry.refresh()} disabled={telemetry.loading} variant="outline" aria-label="重新整理代理執行清單">
                            <RefreshCw size={15} className={telemetry.loading ? "animate-spin motion-reduce:animate-none" : ""} aria-hidden="true" />
                            重新整理
                        </Button>
                    </div>
                </header>

                <div className="grid gap-5 lg:grid-cols-[19rem_minmax(0,1fr)]">
                    <RunList
                        runs={telemetry.runs}
                        selectedRunId={telemetry.selectedRunId}
                        loading={telemetry.loading}
                        onSelect={selectRun}
                    />

                    <section className="min-w-0 rounded-xl border border-border bg-background/60 p-4 sm:p-6" aria-label="代理執行詳情">
                        {telemetry.error && !telemetry.runs.length ? (
                            <div className="flex min-h-72 flex-col items-center justify-center text-center">
                                <AlertTriangle size={26} className="text-amber-600" aria-hidden="true" />
                                <h2 className="mt-3 text-sm font-semibold">無法載入執行清單</h2>
                                <p className="mt-1 max-w-sm text-sm text-muted-foreground">{telemetry.error.message}</p>
                                <Button type="button" onClick={() => void telemetry.refresh()} className="mt-4">重試</Button>
                            </div>
                        ) : telemetry.detailError ? (
                            <div className="flex min-h-72 flex-col items-center justify-center text-center">
                                <AlertTriangle size={26} className="text-amber-600" aria-hidden="true" />
                                <h2 className="mt-3 text-sm font-semibold">無法載入執行詳情</h2>
                                <p className="mt-1 max-w-sm text-sm text-muted-foreground">{telemetry.detailError.message}</p>
                                <Button type="button" onClick={() => void telemetry.refresh()} className="mt-4">重試</Button>
                            </div>
                        ) : telemetry.snapshot ? (
                            <RunDetail snapshot={telemetry.snapshot} events={telemetry.events} detailLoading={telemetry.detailLoading} />
                        ) : (
                            <div className="flex min-h-72 flex-col items-center justify-center text-center">
                                <Clock3 size={26} className="text-muted-foreground/70" aria-hidden="true" />
                                <h2 className="mt-3 text-sm font-semibold">選取一個執行</h2>
                                <p className="mt-1 text-sm text-muted-foreground">從左側清單選擇工作流程以查看節點與事件。</p>
                                {selectedSummary && <p className="mt-3 font-mono text-xs text-muted-foreground">{compactId(selectedSummary.run_id)}</p>}
                            </div>
                        )}
                    </section>
                </div>
            </div>
        </main>
    );
}
