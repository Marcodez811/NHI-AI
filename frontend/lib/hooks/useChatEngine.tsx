"use client";

import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { useParams } from "next/navigation";
import { useChatModels } from "./useChatModels";
import { useChatSession } from "./useChatSession";
import type { ChatSessionState } from "./useChatSession";
import type { ChatModelOption } from "../api/chat";

export type ChatEngineValue = ChatSessionState & {
    models: ChatModelOption[];
    modelsLoading: boolean;
};

const ChatEngineContext = createContext<ChatEngineValue | null>(null);

/**
 * Owns the live chat session above the route level (in the shared workspace
 * layout), not inside the `/chat` or `/chat/[sessionId]` page itself.
 *
 * `router.replace` right after a session is created (see `ensureSession` in
 * `useChatSession`) moves the URL from `/chat` to `/chat/<id>` — a different
 * leaf route, which Next.js unmounts and remounts. If the session hook lived
 * in the page, that remount would drop the reply that is still streaming in.
 * Living in the layout, this provider is untouched by that transition; only
 * `useParams()` here reacts to it, which `useChatSession`'s own dedupe
 * (`selfCreatedIds`) already treats as "no reload needed".
 */
export function ChatEngineProvider({ children }: { children: ReactNode }) {
    const params = useParams<{ sessionId?: string | string[] }>();
    const rawId = params?.sessionId;
    const sessionId = Array.isArray(rawId) ? rawId[0] ?? null : rawId ?? null;

    const { models, defaultModel, loading: modelsLoading } = useChatModels();
    const chat = useChatSession(sessionId, defaultModel);

    return (
        <ChatEngineContext.Provider value={{ ...chat, models, modelsLoading }}>
            {children}
        </ChatEngineContext.Provider>
    );
}

export function useChatEngine(): ChatEngineValue {
    const value = useContext(ChatEngineContext);
    if (!value) throw new Error("useChatEngine must be used inside ChatEngineProvider");
    return value;
}
