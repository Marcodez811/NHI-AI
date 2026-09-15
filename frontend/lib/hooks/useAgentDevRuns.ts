"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
    fetchAgentRun,
    fetchAgentRunEvents,
    fetchAgentRuns,
} from "../api/agents";
import type {
    AgentEvent,
    AgentRunListResponse,
    AgentRunSnapshot,
    AgentRunSummary,
} from "../api/agents";
import { ApiError, getApiErrorMessage } from "../api/client";

export interface UseAgentDevRunsOptions {
    pollIntervalMs?: number;
    runLimit?: number;
    eventLimit?: number;
}

export interface UseAgentDevRunsResult {
    runs: AgentRunSummary[];
    selectedRunId: string | null;
    selectRun: (runId: string) => void;
    snapshot: AgentRunSnapshot | null;
    events: AgentEvent[];
    loading: boolean;
    detailLoading: boolean;
    error: ApiError | null;
    detailError: ApiError | null;
    lastUpdatedAt: Date | null;
    refresh: () => Promise<void>;
}

const DEFAULT_POLL_INTERVAL = 10_000;
const DEFAULT_RUN_LIMIT = 50;
const DEFAULT_EVENT_LIMIT = 200;
const TERMINAL_STATUSES = new Set(["completed", "failed"]);
const LIFECYCLE_EVENT_TYPES = new Set([
    "run_started",
    "phase_changed",
    "node_started",
    "node_completed",
    "node_failed",
    "run_completed",
    "run_failed",
]);

function asApiError(error: unknown, fallback: string): ApiError {
    if (error instanceof ApiError) return error;
    if (error instanceof Error && error.message) return new ApiError(0, error.message);
    return new ApiError(0, getApiErrorMessage(error, fallback));
}

function isTerminal(status: string | undefined | null): boolean {
    return status ? TERMINAL_STATUSES.has(status) : false;
}

/**
 * Polls the gated agent telemetry endpoints. Event cursors are sequence based,
 * so reconnects and overlapping list/detail responses cannot duplicate rows.
 */
export function useAgentDevRuns(
    options: UseAgentDevRunsOptions = {},
): UseAgentDevRunsResult {
    const pollInterval = Math.max(500, options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL);
    const runLimit = Math.max(1, options.runLimit ?? DEFAULT_RUN_LIMIT);
    const eventLimit = Math.max(1, options.eventLimit ?? DEFAULT_EVENT_LIMIT);
    const mounted = useRef(true);
    const selectedRunIdRef = useRef<string | null>(null);
    const terminalRunRef = useRef(false);
    const eventCursor = useRef(0);
    const eventsBySequence = useRef(new Map<number, AgentEvent>());
    const listRequest = useRef<AbortController | null>(null);
    const detailRequest = useRef<AbortController | null>(null);
    const inFlight = useRef(false);

    const [runs, setRuns] = useState<AgentRunSummary[]>([]);
    const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
    const [snapshot, setSnapshot] = useState<AgentRunSnapshot | null>(null);
    const [events, setEvents] = useState<AgentEvent[]>([]);
    const [loading, setLoading] = useState(true);
    const [detailLoading, setDetailLoading] = useState(false);
    const [error, setError] = useState<ApiError | null>(null);
    const [detailError, setDetailError] = useState<ApiError | null>(null);
    const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);

    const resetEvents = useCallback(() => {
        eventCursor.current = 0;
        eventsBySequence.current.clear();
        setEvents([]);
    }, []);

    const selectRun = useCallback((runId: string) => {
        if (!runId || runId === selectedRunIdRef.current) return;
        selectedRunIdRef.current = runId;
        terminalRunRef.current = false;
        setSelectedRunId(runId);
        setSnapshot(null);
        setDetailError(null);
        resetEvents();
    }, [resetEvents]);

    const loadDetail = useCallback(async (runId: string, signal: AbortSignal) => {
        setDetailLoading(true);
        try {
            // Read the snapshot first so a terminal run gives us a fixed
            // sequence target. The event stream is paginated, and fetching
            // only one page can otherwise leave a terminal run incomplete.
            const nextSnapshot = await fetchAgentRun(runId, { signal });
            if (typeof window === "undefined" || !mounted.current || signal.aborted || selectedRunIdRef.current !== runId) return;

            let page = await fetchAgentRunEvents(runId, {
                after: eventCursor.current,
                limit: eventLimit,
                signal,
            });
            let cursor = eventCursor.current;

            const consumePage = (events: AgentEvent[], nextAfter: number | null) => {
                const previousCursor = cursor;
                for (const event of events) {
                    if (event.sequence > cursor) cursor = event.sequence;
                    if (LIFECYCLE_EVENT_TYPES.has(event.event_type)) {
                        eventsBySequence.current.set(event.sequence, event);
                    }
                }
                if (nextAfter !== null && nextAfter > cursor) cursor = nextAfter;
                return cursor > previousCursor;
            };

            consumePage(page.events, page.next_after);
            while (cursor < nextSnapshot.last_sequence && page.events.length > 0) {
                const previousCursor = cursor;
                page = await fetchAgentRunEvents(runId, {
                    after: cursor,
                    limit: eventLimit,
                    signal,
                });
                if (typeof window === "undefined" || !mounted.current || signal.aborted || selectedRunIdRef.current !== runId) return;
                const advanced = consumePage(page.events, page.next_after);
                if (!advanced || cursor <= previousCursor) break;
            }

            if (typeof window === "undefined" || !mounted.current || signal.aborted || selectedRunIdRef.current !== runId) return;
            eventCursor.current = cursor;
            setSnapshot(nextSnapshot);
            terminalRunRef.current = isTerminal(nextSnapshot.status) && cursor >= nextSnapshot.last_sequence;
            setDetailError(null);
            setEvents(
                [...eventsBySequence.current.values()].sort((a, b) => a.sequence - b.sequence),
            );
            setLastUpdatedAt(new Date());
        } catch (requestError) {
            if (typeof window === "undefined" || !mounted.current || signal.aborted) return;
            setDetailError(asApiError(requestError, "代理執行詳情暫時無法載入。"));
        } finally {
            if (typeof window !== "undefined" && mounted.current) setDetailLoading(false);
        }
    }, [eventLimit]);

    const refreshRuns = useCallback(async (forceDetail = false) => {
        if (!mounted.current || inFlight.current) return;
        inFlight.current = true;
        const controller = new AbortController();
        listRequest.current = controller;
        try {
            const response: AgentRunListResponse = await fetchAgentRuns(runLimit, {
                signal: controller.signal,
            });
            if (typeof window === "undefined" || !mounted.current || controller.signal.aborted) return;
            setRuns(response.runs);
            setError(null);
            setLoading(false);
            setLastUpdatedAt(new Date());

            const currentId = selectedRunIdRef.current;
            const nextSelected = currentId && response.runs.some((run) => run.run_id === currentId)
                ? currentId
                : response.runs[0]?.run_id ?? null;
            if (nextSelected && nextSelected !== currentId) {
                selectedRunIdRef.current = nextSelected;
                terminalRunRef.current = false;
                setSelectedRunId(nextSelected);
                setSnapshot(null);
                setDetailError(null);
                resetEvents();
            }
            if (nextSelected && (forceDetail || !terminalRunRef.current)) {
                detailRequest.current?.abort();
                const detailController = new AbortController();
                detailRequest.current = detailController;
                await loadDetail(nextSelected, detailController.signal);
            } else if (!nextSelected) {
                selectedRunIdRef.current = null;
                terminalRunRef.current = false;
                setSelectedRunId(null);
                setSnapshot(null);
                resetEvents();
            }
        } catch (requestError) {
            if (typeof window === "undefined" || !mounted.current || controller.signal.aborted) return;
            setError(asApiError(requestError, "代理執行清單暫時無法載入。"));
            setLoading(false);
        } finally {
            inFlight.current = false;
        }
    }, [loadDetail, resetEvents, runLimit]);

    const refresh = useCallback(async () => {
        await refreshRuns(true);
    }, [refreshRuns]);

    useEffect(() => {
        mounted.current = true;
        void refreshRuns();
        const timer = window.setInterval(() => void refreshRuns(), pollInterval);
        return () => {
            mounted.current = false;
            window.clearInterval(timer);
            listRequest.current?.abort();
            detailRequest.current?.abort();
        };
    }, [pollInterval, refreshRuns]);

    return {
        runs,
        selectedRunId,
        selectRun,
        snapshot,
        events,
        loading,
        detailLoading,
        error,
        detailError,
        lastUpdatedAt,
        refresh,
    };
}
