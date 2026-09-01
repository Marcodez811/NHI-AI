import { Cpu, Server } from "lucide-react";
import type { AgentRunSummary } from "../../../lib/api/agents";
import { Button } from "../../ui/button";
import { compactId, formatDateTime, statusDotClass, statusLabel, statusTextClass } from "./format";

interface RunListProps {
    runs: AgentRunSummary[];
    selectedRunId: string | null;
    loading: boolean;
    onSelect: (runId: string) => void;
}

function RunListItem({ run, selected, onSelect }: { run: AgentRunSummary; selected: boolean; onSelect: () => void }) {
    return (
        <Button
            type="button"
            onClick={onSelect}
            variant="ghost"
            aria-current={selected ? "true" : undefined}
            className={`h-auto w-full justify-start rounded-none border-b border-border/70 px-4 py-3 text-left last:border-b-0 hover:bg-accent/70 focus-visible:bg-accent ${selected ? "bg-primary/8" : ""}`}
        >
            <span className="block min-w-0 w-full">
                <span className="flex items-center gap-2">
                    <span className={`size-2 rounded-full ${statusDotClass(run.status)}`} aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate text-sm font-semibold">{run.workflow}</span>
                    <span className={`text-[11px] font-medium ${statusTextClass(run.status)}`}>{statusLabel(run.status)}</span>
                </span>
                <span className="mt-1 flex items-center justify-between gap-3 text-[11px] text-muted-foreground">
                    <span className="truncate font-mono">{compactId(run.run_id)}</span>
                    <span className="shrink-0">{formatDateTime(run.updated_at || run.started_at)}</span>
                </span>
            </span>
        </Button>
    );
}

function RunSkeleton() {
    return (
        <div className="space-y-3 p-4" aria-hidden="true">
            {[1, 2, 3, 4].map((item) => (
                <div key={item} className="space-y-2">
                    <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
                    <div className="h-2.5 w-full animate-pulse rounded bg-muted" />
                </div>
            ))}
        </div>
    );
}

export function RunList({ runs, selectedRunId, loading, onSelect }: RunListProps) {
    return (
        <aside className="overflow-hidden rounded-xl border border-border bg-card shadow-sm" aria-label="代理執行清單">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
                <div className="flex items-center gap-2">
                    <Server size={15} className="text-primary" aria-hidden="true" />
                    <h2 className="text-sm font-semibold">Recent runs</h2>
                </div>
                <span className="rounded-full bg-secondary px-2 py-0.5 text-[11px] font-medium text-muted-foreground">{runs.length}</span>
            </div>
            {loading && !runs.length ? (
                <RunSkeleton />
            ) : runs.length ? (
                <div>{runs.map((run) => <RunListItem key={run.run_id} run={run} selected={run.run_id === selectedRunId} onSelect={() => onSelect(run.run_id)} />)}</div>
            ) : (
                <div className="px-4 py-12 text-center">
                    <Cpu size={24} className="mx-auto text-muted-foreground/60" aria-hidden="true" />
                    <p className="mt-3 text-sm font-medium">尚無代理執行紀錄</p>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">啟用工作流程後，最近的執行會顯示在這裡。</p>
                </div>
            )}
        </aside>
    );
}
