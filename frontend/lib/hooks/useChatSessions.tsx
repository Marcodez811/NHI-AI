"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import {
    deleteChatSession,
    fetchChatSessions,
    renameChatSession,
} from "../api/chat";
import type { ChatSessionSummary } from "../api/chat";

function errorText(error: unknown): string {
    return error instanceof ApiError ? error.message : "服務暫時無法使用，請稍後再試。";
}

export interface ChatSessionsContextValue {
    sessions: ChatSessionSummary[];
    loading: boolean;
    error: string | null;
    refresh: () => Promise<void>;
    rename: (id: string, title: string) => Promise<void>;
    remove: (id: string) => Promise<void>;
}

/**
 * A non-throwing default lets the sidebar render outside the provider (e.g.
 * in isolated component tests) with an empty, inert list instead of crashing.
 */
const defaultValue: ChatSessionsContextValue = {
    sessions: [],
    loading: false,
    error: null,
    refresh: async () => undefined,
    rename: async () => undefined,
    remove: async () => undefined,
};

const ChatSessionsContext = createContext<ChatSessionsContextValue>(defaultValue);

/** Keeps the sidebar's recent-conversation list current across chat routes. */
export function ChatSessionsProvider({ children }: { children: ReactNode }) {
    const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const refresh = useCallback(async () => {
        try {
            setError(null);
            setSessions(await fetchChatSessions());
        } catch (caught) {
            setError(errorText(caught));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    const rename = useCallback(async (id: string, title: string) => {
        const updated = await renameChatSession(id, title);
        setSessions((items) => items.map((item) => (item.id === id ? { ...item, title: updated.title } : item)));
    }, []);

    const remove = useCallback(async (id: string) => {
        await deleteChatSession(id);
        setSessions((items) => items.filter((item) => item.id !== id));
    }, []);

    return (
        <ChatSessionsContext.Provider value={{ sessions, loading, error, refresh, rename, remove }}>
            {children}
        </ChatSessionsContext.Provider>
    );
}

export function useChatSessions(): ChatSessionsContextValue {
    return useContext(ChatSessionsContext);
}
