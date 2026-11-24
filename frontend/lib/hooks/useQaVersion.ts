"use client";

import { useEffect, useState } from "react";
import { fetchQaVersion } from "../api/qa";
import type { QaVersionContent } from "../api/qa";
import { ApiError } from "../api/client";

export type QaVersionState = { content: QaVersionContent | null; error: string | null; loading: boolean };

// A produced version never changes, so its content is fetched once per page load.
const cache = new Map<string, QaVersionContent>();

export const clearQaVersionCache = () => cache.clear();

const cacheKey = (sessionId: string, artifactId: string) => `${sessionId}/${artifactId}`;

/** The structured content of one version; ``artifactId`` null yields an idle state. */
export function useQaVersion(sessionId: string | null, artifactId: string | null): QaVersionState {
    const key = sessionId && artifactId ? cacheKey(sessionId, artifactId) : null;
    const [fetched, setFetched] = useState<{ key: string; content: QaVersionContent } | null>(null);
    const [failed, setFailed] = useState<{ key: string; message: string } | null>(null);
    useEffect(() => {
        if (!key || !sessionId || !artifactId || cache.has(key)) return;
        const controller = new AbortController();
        fetchQaVersion(sessionId, artifactId, { signal: controller.signal })
            .then((content) => {
                cache.set(key, content);
                setFetched({ key, content });
            })
            .catch((caught: unknown) => {
                if (controller.signal.aborted) return;
                setFailed({ key, message: caught instanceof ApiError ? caught.message : "無法載入這個版本。" });
            });
        return () => controller.abort();
    }, [artifactId, key, sessionId]);
    const content = key ? (cache.get(key) ?? (fetched?.key === key ? fetched.content : null)) : null;
    const error = key && !content && failed?.key === key ? failed.message : null;
    return { content, error, loading: Boolean(key) && !content && !error };
}
