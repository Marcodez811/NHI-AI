"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { fetchChatModels } from "../api/chat";
import type { ChatModelOption } from "../api/chat";

function errorText(error: unknown): string {
    return error instanceof ApiError ? error.message : "服務暫時無法使用，請稍後再試。";
}

/** The chat box's model picker: `GET /chat/models`, with availability driven by server-side keys. */
export function useChatModels() {
    const [models, setModels] = useState<ChatModelOption[]>([]);
    const [defaultModel, setDefaultModel] = useState<string | undefined>(undefined);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            setError(null);
            const response = await fetchChatModels();
            setModels(response.models);
            setDefaultModel(response.default);
        } catch (caught) {
            setError(errorText(caught));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    return { models, defaultModel, loading, error, reload: load };
}
