import type { AgentJobPhase } from "../api/types";

const VALID_PHASES: readonly AgentJobPhase[] = [
    "queued",
    "preparing",
    "extracting",
    "drafting",
    "validating",
    "reviewing",
    "revising",
    "publishing",
    "completed",
    "failed",
];

export function readActiveJobId(storageKey: string): string | null {
    if (typeof window === "undefined") return null;
    try {
        return window.sessionStorage.getItem(storageKey);
    } catch {
        return null;
    }
}

export function writeActiveJobId(storageKey: string, id: string | null): void {
    if (typeof window === "undefined") return;
    try {
        if (id) window.sessionStorage.setItem(storageKey, id);
        else window.sessionStorage.removeItem(storageKey);
    } catch {
        // Persistence is optional; restricted storage must not break a job.
    }
}

export function readPhaseHistory(storageKey: string): AgentJobPhase[] {
    if (typeof window === "undefined") return [];
    try {
        const value: unknown = JSON.parse(
            window.sessionStorage.getItem(`${storageKey}:phases`) || "[]",
        );
        return Array.isArray(value)
            ? value.filter((phase): phase is AgentJobPhase =>
                  typeof phase === "string" && VALID_PHASES.includes(phase as AgentJobPhase),
              )
            : [];
    } catch {
        return [];
    }
}

export function writePhaseHistory(storageKey: string, history: AgentJobPhase[]): void {
    if (typeof window === "undefined") return;
    try {
        if (history.length) {
            window.sessionStorage.setItem(`${storageKey}:phases`, JSON.stringify(history));
        } else {
            window.sessionStorage.removeItem(`${storageKey}:phases`);
        }
    } catch {
        // Persistence is optional; restricted storage must not break a job.
    }
}

export function phaseForJob(job: {
    phase?: AgentJobPhase | null;
    status: string;
}): AgentJobPhase | null {
    if (job.phase) return job.phase;
    if (job.status === "queued") return "queued";
    if (job.status === "completed") return "completed";
    if (job.status === "failed") return "failed";
    return "preparing";
}

export function appendPhase(
    history: AgentJobPhase[],
    phase: AgentJobPhase | null,
): AgentJobPhase[] {
    if (!phase || history[history.length - 1] === phase) return history;
    return [...history, phase];
}
