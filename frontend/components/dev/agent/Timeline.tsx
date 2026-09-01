import type { AgentEvent } from "../../../lib/api/agents";
import { eventLabel, formatDateTime, statusDotClass } from "./format";

export function Timeline({ events }: { events: AgentEvent[] }) {
    if (!events.length) {
        return <div className="rounded-lg border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">尚未收到事件。</div>;
    }

    return (
        <ol className="divide-y divide-border/70 rounded-lg border border-border bg-card">
            {events.map((event) => (
                <li key={event.sequence} className="grid gap-2 px-4 py-3 sm:grid-cols-[5.5rem_7rem_minmax(0,1fr)] sm:items-baseline sm:gap-4">
                    <time className="font-mono text-[11px] text-muted-foreground" dateTime={event.occurred_at}>{formatDateTime(event.occurred_at)}</time>
                    <div className="flex min-w-0 items-center gap-2">
                        <span className={`size-1.5 shrink-0 rounded-full ${statusDotClass(event.status)}`} aria-hidden="true" />
                        <span className="truncate text-xs font-medium">{event.node_id || "workflow"}</span>
                    </div>
                    <div className="min-w-0 text-sm">
                        <span className="mr-2 text-xs font-medium text-primary">{eventLabel(event.event_type)}</span>
                        <span className="text-muted-foreground">{event.message || "—"}</span>
                        <span className="ml-2 font-mono text-[10px] text-muted-foreground/70">#{event.sequence}</span>
                    </div>
                </li>
            ))}
        </ol>
    );
}
