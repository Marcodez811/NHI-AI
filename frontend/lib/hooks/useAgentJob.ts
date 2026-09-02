"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { AgentJobPhase } from "../api/types";
import { ApiError, getApiErrorMessage } from "../api/client";

export type AgentJobClientPhase =
    | "idle"
    | "submitting"
    | "polling"
    | "completed"
    | "failed"
    | "expired";

export interface AgentJobRecord {
    job_id: string;
    status: string;
    phase?: AgentJobPhase | null;
    stage?: string | null;
    message?: string | null;
    started_at?: string | null;
    finished_at?: string | null;
    error?: string | null;
    download_url?: string | null;
}

export interface AgentJobCreated {
    job_id: string;
    status: string;
    phase?: AgentJobPhase;
}

export interface UseAgentJobOptions<Payload, Job extends AgentJobRecord, Created extends AgentJobCreated = AgentJobCreated> {
    createJob: (
        payload: Payload,
        options?: { signal?: AbortSignal },
    ) => Promise<Created>;
    getJob: (
        id: string,
        options?: { signal?: AbortSignal },
    ) => Promise<Job>;
    /** Delay between healthy polls. Defaults to two seconds. */
    pollIntervalMs?: number;
    /** Maximum delay after transient poll failures. Defaults to ten seconds. */
    maxPollIntervalMs?: number;
    /** Session-storage key used to resume a job after a page refresh. */
    storageKey?: string;
    terminalStatuses?: readonly string[];
}

export interface UseAgentJobResult<Payload, Job extends AgentJobRecord, Created extends AgentJobCreated = AgentJobCreated> {
    job: Job | null;
    phase: AgentJobClientPhase;
    /** Stable phases observed during this browser session. */
    phaseHistory: AgentJobPhase[];
    submitting: boolean;
    polling: boolean;
    error: ApiError | null;
    warning: string | null;
    consecutivePollFailures: number;
    startJob: (payload: Payload) => Promise<Created>;
    start: (payload: Payload) => Promise<Created>;
    pollNow: () => Promise<Job | undefined>;
    retry: (payload?: Payload) => Promise<Created | null>;
    reset: () => void;
}

const DEFAULT_STORAGE_KEY = "nhi-ai:active-agent-job-id";
const DEFAULT_TERMINAL_STATUSES = ["completed", "failed"] as const;
const TRANSIENT_STATUS_WARNING = "Status temporarily unavailable—retrying.";

function asApiError(error: unknown, fallback: string): ApiError {
    if (error instanceof ApiError) return error;
    if (error instanceof Error && error.message) {
        return new ApiError(0, error.message);
    }
    return new ApiError(0, getApiErrorMessage(error, fallback));
}

function readActiveJobId(storageKey: string): string | null {
    if (typeof window === "undefined") return null;
    try {
        return window.sessionStorage.getItem(storageKey);
    } catch {
        return null;
    }
}

function writeActiveJobId(storageKey: string, id: string | null): void {
    if (typeof window === "undefined") return;
    try {
        if (id) window.sessionStorage.setItem(storageKey, id);
        else window.sessionStorage.removeItem(storageKey);
    } catch {
        // Session storage is an enhancement; private browsing must not break polling.
    }
}

function readPhaseHistory(storageKey: string): AgentJobPhase[] {
    if (typeof window === "undefined") return [];
    try {
        const value: unknown = JSON.parse(
            window.sessionStorage.getItem(`${storageKey}:phases`) || "[]",
        );
        return Array.isArray(value)
            ? value.filter((phase): phase is AgentJobPhase =>
                  typeof phase === "string" &&
                  [
                      "queued",
                      "preparing",
                      "drafting",
                      "validating",
                      "reviewing",
                      "revising",
                      "publishing",
                      "completed",
                      "failed",
                  ].includes(phase),
              )
            : [];
    } catch {
        return [];
    }
}

function writePhaseHistory(storageKey: string, history: AgentJobPhase[]): void {
    if (typeof window === "undefined") return;
    try {
        if (history.length) {
            window.sessionStorage.setItem(
                `${storageKey}:phases`,
                JSON.stringify(history),
            );
        } else {
            window.sessionStorage.removeItem(`${storageKey}:phases`);
        }
    } catch {
        // Session storage is an enhancement; private browsing must not break polling.
    }
}

function phaseForJob(job: AgentJobRecord): AgentJobPhase | null {
    if (job.phase) return job.phase;
    if (job.status === "queued") return "queued";
    if (job.status === "completed") return "completed";
    if (job.status === "failed") return "failed";
    return "preparing";
}

function appendPhase(history: AgentJobPhase[], phase: AgentJobPhase | null): AgentJobPhase[] {
    if (!phase || history[history.length - 1] === phase) return history;
    return [...history, phase];
}

/**
 * Workflow-neutral async job lifecycle. A workflow supplies only its create
 * and status requests; retention, backoff, reconnect, and terminal handling
 * stay consistent for slides and future agentic workflows.
 */
export function useAgentJob<Payload, Job extends AgentJobRecord, Created extends AgentJobCreated = AgentJobCreated>(
    options: UseAgentJobOptions<Payload, Job, Created>,
): UseAgentJobResult<Payload, Job, Created> {
    const pollInterval = Math.max(250, options.pollIntervalMs ?? 2_000);
    const maxPollInterval = Math.max(
        pollInterval,
        options.maxPollIntervalMs ?? 10_000,
    );
    const storageKey = options.storageKey ?? DEFAULT_STORAGE_KEY;
    const terminalStatuses = options.terminalStatuses ?? DEFAULT_TERMINAL_STATUSES;

    const mounted = useRef(true);
    const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const requestController = useRef<AbortController | null>(null);
    const generation = useRef(0);
    const activeJobId = useRef<string | null>(null);
    const lastPayload = useRef<Payload | null>(null);
    const failureCount = useRef(0);
    const resumed = useRef(false);
    const createJobRef = useRef(options.createJob);
    const getJobRef = useRef(options.getJob);
    createJobRef.current = options.createJob;
    getJobRef.current = options.getJob;

    const [job, setJob] = useState<Job | null>(null);
    const [phase, setPhase] = useState<AgentJobClientPhase>("idle");
    const [phaseHistory, setPhaseHistory] = useState<AgentJobPhase[]>([]);
    const [submitting, setSubmitting] = useState(false);
    const [polling, setPolling] = useState(false);
    const [error, setError] = useState<ApiError | null>(null);
    const [warning, setWarning] = useState<string | null>(null);
    const [consecutivePollFailures, setConsecutivePollFailures] = useState(0);

    const cancelPending = useCallback(() => {
        generation.current += 1;
        if (timer.current !== null) {
            clearTimeout(timer.current);
            timer.current = null;
        }
        requestController.current?.abort();
        requestController.current = null;
    }, []);

    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            cancelPending();
        };
    }, [cancelPending]);

    const finish = useCallback((nextPhase: AgentJobClientPhase) => {
        if (!mounted.current) return;
        setPhase(nextPhase);
        setPolling(false);
        if (timer.current !== null) {
            clearTimeout(timer.current);
            timer.current = null;
        }
    }, []);

    const pollForRef = useRef<(id: string, token: number) => Promise<Job | undefined>>(
        async () => undefined,
    );

    const schedule = useCallback((id: string, delay: number, token: number) => {
        if (!mounted.current || token !== generation.current) return;
        if (timer.current !== null) clearTimeout(timer.current);
        setPolling(true);
        timer.current = setTimeout(() => {
            timer.current = null;
            void pollForRef.current(id, token);
        }, delay);
    }, []);

    const pollFor = useCallback(async (
        id: string,
        token: number,
    ): Promise<Job | undefined> => {
        if (!mounted.current || token !== generation.current) return undefined;
        const controller = new AbortController();
        requestController.current = controller;
        try {
            const next = await getJobRef.current(id, { signal: controller.signal });
            if (!mounted.current || token !== generation.current) return undefined;
            // A malformed or exhausted transport response must behave like a
            // transient poll failure, never crash the lifecycle updater.
            if (!next) {
                throw new ApiError(0, "Agent job status response was empty.");
            }
            failureCount.current = 0;
            setConsecutivePollFailures(0);
            setWarning(null);
            setJob(next);
            setPhaseHistory((history) => {
                const nextHistory = appendPhase(history, phaseForJob(next));
                writePhaseHistory(storageKey, nextHistory);
                return nextHistory;
            });
            if (terminalStatuses.includes(next.status)) {
                setError(next.status === "failed"
                    ? new ApiError(0, next.error || next.message || "Agent workflow failed.")
                    : null);
                finish(next.status === "completed" ? "completed" : "failed");
            } else {
                setPhase("polling");
                schedule(id, pollInterval, token);
            }
            return next;
        } catch (requestError) {
            if (
                !mounted.current ||
                token !== generation.current ||
                (requestError instanceof DOMException && requestError.name === "AbortError")
            ) {
                return undefined;
            }
            const apiError = asApiError(requestError, "Agent job status is temporarily unavailable.");
            if (apiError.status === 404) {
                writeActiveJobId(storageKey, null);
                writePhaseHistory(storageKey, []);
                setError(new ApiError(404, "This agent job has expired or is no longer available."));
                setWarning(null);
                finish("expired");
                return undefined;
            }
            failureCount.current += 1;
            const failures = failureCount.current;
            setConsecutivePollFailures(failures);
            // Keep the last successful job in state. Only the connection hint changes.
            setWarning(TRANSIENT_STATUS_WARNING);
            setPhase("polling");
            const delay = Math.min(
                maxPollInterval,
                pollInterval * 2 ** Math.max(0, failures - 1),
            );
            schedule(id, delay, token);
            return undefined;
        } finally {
            if (requestController.current === controller) {
                requestController.current = null;
            }
        }
    }, [finish, maxPollInterval, pollInterval, schedule, storageKey, terminalStatuses]);

    pollForRef.current = pollFor;

    // A refresh has no in-memory job, so resume with an immediate status read.
    useEffect(() => {
        if (resumed.current) return;
        resumed.current = true;
        const storedId = readActiveJobId(storageKey);
        if (!storedId) return;
        activeJobId.current = storedId;
        setPhaseHistory(readPhaseHistory(storageKey));
        setPhase("polling");
        void pollForRef.current(storedId, generation.current);
    }, [storageKey]);

    const startJob = useCallback(async (
        payload: Payload,
    ): Promise<Created> => {
        cancelPending();
        const token = generation.current;
        lastPayload.current = payload;
        activeJobId.current = null;
        writeActiveJobId(storageKey, null);
        writePhaseHistory(storageKey, []);
        failureCount.current = 0;
        setConsecutivePollFailures(0);
        setJob(null);
        setPhaseHistory([]);
        setError(null);
        setWarning(null);
        setPhase("submitting");
        setSubmitting(true);
        const controller = new AbortController();
        requestController.current = controller;
        try {
            const created = await createJobRef.current(payload, { signal: controller.signal });
            if (!mounted.current || token !== generation.current) return created;
            activeJobId.current = created.job_id;
            writeActiveJobId(storageKey, created.job_id);
            const createdPhase = created.phase ?? (created.status === "queued" ? "queued" : "preparing");
            setPhaseHistory([createdPhase]);
            writePhaseHistory(storageKey, [createdPhase]);
            setJob({
                job_id: created.job_id,
                status: created.status,
                phase: createdPhase,
                stage: null,
                message: null,
                started_at: null,
                finished_at: null,
                error: null,
                download_url: null,
            } as Job);
            setPhase("polling");
            setPolling(true);
            schedule(created.job_id, pollInterval, token);
            return created;
        } catch (requestError) {
            if (
                requestError instanceof DOMException &&
                requestError.name === "AbortError"
            ) {
                throw requestError;
            }
            const apiError = asApiError(requestError, "Agent job could not be created.");
            if (mounted.current && token === generation.current) {
                setError(apiError);
                setPhase("idle");
                setPolling(false);
            }
            throw apiError;
        } finally {
            if (requestController.current === controller) requestController.current = null;
            if (mounted.current && token === generation.current) setSubmitting(false);
        }
    }, [cancelPending, pollInterval, schedule, storageKey]);

    const pollNow = useCallback(async (): Promise<Job | undefined> => {
        const id = activeJobId.current ?? job?.job_id;
        if (!id || (job && terminalStatuses.includes(job.status))) return job ?? undefined;
        if (timer.current !== null) {
            clearTimeout(timer.current);
            timer.current = null;
        }
        return pollForRef.current(id, generation.current);
    }, [job, terminalStatuses]);

    const retry = useCallback(async (payload?: Payload): Promise<Created | null> => {
        const nextPayload = payload ?? lastPayload.current;
        if (!nextPayload) return null;
        return startJob(nextPayload);
    }, [startJob]);

    const reset = useCallback(() => {
        cancelPending();
        activeJobId.current = null;
        lastPayload.current = null;
        writeActiveJobId(storageKey, null);
        writePhaseHistory(storageKey, []);
        failureCount.current = 0;
        setJob(null);
        setPhase("idle");
        setPhaseHistory([]);
        setSubmitting(false);
        setPolling(false);
        setError(null);
        setWarning(null);
        setConsecutivePollFailures(0);
    }, [cancelPending, storageKey]);

    return {
        job,
        phase,
        phaseHistory,
        submitting,
        polling,
        error,
        warning,
        consecutivePollFailures,
        startJob,
        start: startJob,
        pollNow,
        retry,
        reset,
    };
}

export { TRANSIENT_STATUS_WARNING };
