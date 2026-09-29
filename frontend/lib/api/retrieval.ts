/** Retrieval index readiness contract. */
import { request } from "./client";

/** Sanitized readiness contract for the application's retrieval index. */
export type RetrievalIndexState =
    | "uninitialized"
    | "provisioning"
    | "ready"
    | "error";

export type RetrievalIndexErrorCode =
    | "invalid_seed"
    | "provider_unavailable"
    | "provider_not_configured"
    | "store_missing_or_expired"
    | (string & {});

export interface RetrievalStatus {
    state: RetrievalIndexState;
    can_retrieve: boolean;
    ready_document_count: number;
    error_code?: RetrievalIndexErrorCode | null;
    warning_code?: string | null;
}

export async function fetchRetrievalStatus(
    options: { signal?: AbortSignal } = {},
): Promise<RetrievalStatus> {
    return request<RetrievalStatus>("/retrieval/status", options);
}

