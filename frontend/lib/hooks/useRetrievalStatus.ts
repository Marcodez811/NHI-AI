"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import {
    fetchRetrievalStatus,
    type RetrievalStatus,
} from "../api/retrieval";

export interface UseRetrievalStatusOptions {
    autoLoad?: boolean;
    pollIntervalMs?: number;
}

export interface UseRetrievalStatusResult {
    status: RetrievalStatus | null;
    loading: boolean;
    error: ApiError | null;
    reload: () => Promise<RetrievalStatus | null>;
}

function toApiError(error: unknown): ApiError {
    if (error instanceof ApiError) return error;
    if (error instanceof Error && error.message) {
        return new ApiError(0, error.message);
    }
    return new ApiError(0, "知識庫狀態暫時無法取得。");
}

/**
 * Loads the server-owned retrieval bootstrap state and refreshes only while
 * the backend is still creating or discovering its vector store.
 */
export function useRetrievalStatus(
    options: UseRetrievalStatusOptions = {},
): UseRetrievalStatusResult {
    const autoLoad = options.autoLoad ?? true;
    const pollIntervalMs = options.pollIntervalMs ?? 2500;
    const mounted = useRef(true);
    const [status, setStatus] = useState<RetrievalStatus | null>(null);
    const [loading, setLoading] = useState(autoLoad);
    const [error, setError] = useState<ApiError | null>(null);

    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    const reload = useCallback(async (): Promise<RetrievalStatus | null> => {
        setLoading(true);
        setError(null);
        try {
            const next = await fetchRetrievalStatus();
            if (mounted.current) setStatus(next);
            return next;
        } catch (requestError) {
            const apiError = toApiError(requestError);
            if (mounted.current) setError(apiError);
            return null;
        } finally {
            if (mounted.current) setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (autoLoad) void reload();
    }, [autoLoad, reload]);

    useEffect(() => {
        if (
            !status ||
            (status.state !== "uninitialized" && status.state !== "provisioning")
        ) {
            return;
        }
        const timer = window.setTimeout(() => void reload(), pollIntervalMs);
        return () => window.clearTimeout(timer);
    }, [pollIntervalMs, reload, status]);

    return { status, loading, error, reload };
}

