/** Agent telemetry and dashboard transport contracts. */
import { queryString, request } from "./client";

/** Stable, workflow-facing lifecycle phases. Details stay server-side. */
export type AgentJobPhase =
    | "queued"
    | "preparing"
    | "extracting"
    | "planning"
    | "awaiting_outline"
    | "drafting"
    | "validating"
    | "reviewing"
    | "revising"
    | "publishing"
    | "completed"
    | "failed";

/** Sanitized developer telemetry returned by the gated agent-run endpoints. */
export type AgentEventType =
    | "run_started"
    | "phase_changed"
    | "node_started"
    | "node_progress"
    | "node_completed"
    | "node_failed"
    | "heartbeat"
    | "run_completed"
    | "run_failed"
    | (string & {});

export type AgentRunStatus = "queued" | "running" | "completed" | "failed" | (string & {});
export type AgentNodeStatus = "pending" | "waiting" | "running" | "completed" | "failed" | (string & {});

export interface AgentRunSummary {
    run_id: string;
    workflow: string;
    status: AgentRunStatus;
    phase: AgentJobPhase | string | null;
    runner: string | null;
    task_id: string | null;
    worker_id: string | null;
    started_at: string | null;
    updated_at: string | null;
    finished_at: string | null;
    duration_ms: number | null;
    message: string | null;
    last_heartbeat_at: string | null;
    last_sequence: number;
}

export interface AgentNodeSnapshot {
    node_id: string;
    agent_role: string | null;
    runner: string | null;
    model: string | null;
    reasoning_effort: string | null;
    status: AgentNodeStatus;
    attempt: number | null;
    task_id: string | null;
    worker_id: string | null;
    provider_run_id: string | null;
    started_at: string | null;
    updated_at: string | null;
    finished_at: string | null;
    duration_ms: number | null;
    message: string | null;
    last_heartbeat_at: string | null;
}

export interface AgentRunSnapshot extends AgentRunSummary {
    nodes: AgentNodeSnapshot[];
}

export interface AgentEvent {
    run_id: string;
    workflow: string;
    node_id: string | null;
    agent_role: string | null;
    runner: string | null;
    model: string | null;
    reasoning_effort: string | null;
    worker_id: string | null;
    attempt: number | null;
    sequence: number;
    event_type: AgentEventType;
    status: string | null;
    phase: AgentJobPhase | string | null;
    message: string | null;
    occurred_at: string;
    duration_ms: number | null;
    metadata: Record<string, string | number | boolean | null>;
}

export interface AgentRunListResponse {
    runs: AgentRunSnapshot[];
}

export interface AgentEventListResponse {
    events: AgentEvent[];
    after: number;
    next_after: number | null;
}

export async function fetchAgentRuns(
    limit = 50,
    options: { signal?: AbortSignal } = {},
): Promise<AgentRunListResponse> {
    return request<AgentRunListResponse>(`/dev/agent-runs${queryString({ limit })}`, options);
}

export async function fetchAgentRun(
    runId: string,
    options: { signal?: AbortSignal } = {},
): Promise<AgentRunSnapshot> {
    return request<AgentRunSnapshot>(
        `/dev/agent-runs/${encodeURIComponent(runId)}`,
        options,
    );
}

export async function fetchAgentRunEvents(
    runId: string,
    params: { after?: number; limit?: number; signal?: AbortSignal } = {},
): Promise<AgentEventListResponse> {
    const { signal, ...query } = params;
    return request<AgentEventListResponse>(
        `/dev/agent-runs/${encodeURIComponent(runId)}/events${queryString(query)}`,
        { signal },
    );
}

