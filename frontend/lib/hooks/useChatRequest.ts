"use client";

import { useEffect, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { ApiError } from "../api/client";
import { streamChat } from "../api/chat";
import type { ChatRequest } from "../api/chat";
import type { ChatMessage } from "../workspace/types";

let fallbackId = 0;

function newMessageId(role: ChatMessage["role"]): string {
    return globalThis.crypto?.randomUUID?.() ?? `${role}-${Date.now()}-${++fallbackId}`;
}

function errorText(error: unknown): string {
    return error instanceof ApiError
        ? error.message
        : "服務暫時無法使用，請稍後再試。";
}

type UseChatRequestOptions = {
    draft: string;
    setDraft: Dispatch<SetStateAction<string>>;
    setChat: Dispatch<SetStateAction<ChatMessage[]>>;
};

/** Coordinates exactly one chat request and updates only its assistant message. */
export function useChatRequest({ draft, setDraft, setChat }: UseChatRequestOptions) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const draftRef = useRef(draft);
    const activeRequest = useRef(0);
    const inFlight = useRef(false);
    const controller = useRef<AbortController | null>(null);
    const mounted = useRef(true);
    draftRef.current = draft;

    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
            activeRequest.current += 1;
            inFlight.current = false;
            controller.current?.abort();
        };
    }, []);

    const send = async (payload: ChatRequest): Promise<boolean> => {
        const question = payload.question.trim();
        if (!question || inFlight.current) return false;

        const requestId = ++activeRequest.current;
        const userId = newMessageId("user");
        const assistantId = newMessageId("assistant");
        const requestController = new AbortController();
        controller.current = requestController;
        inFlight.current = true;
        draftRef.current = "";
        setDraft("");
        setError(null);
        setBusy(true);
        setChat((messages) => [
            ...messages,
            { id: userId, role: "user", text: question },
            { id: assistantId, role: "assistant", text: "", status: "preparing" },
        ]);

        const updateAssistant = (update: (message: ChatMessage) => ChatMessage) => {
            if (activeRequest.current !== requestId) return;
            setChat((messages) => messages.map((message) =>
                message.id === assistantId ? update(message) : message,
            ));
        };

        try {
            await streamChat(
                { ...payload, question },
                {
                    onStatus: (status) => updateAssistant((message) => ({
                        ...message,
                        status,
                    })),
                    onDelta: (delta) => updateAssistant((message) => ({
                        ...message,
                        text: message.text + delta,
                    })),
                    onDone: (citations) => updateAssistant((message) => ({
                        ...message,
                        citations,
                        status: undefined,
                    })),
                },
                { signal: requestController.signal },
            );
            return true;
        } catch (caught) {
            if (activeRequest.current !== requestId || !mounted.current) return false;
            setError(errorText(caught));
            setChat((messages) => messages.filter((message) => message.id !== assistantId));
            if (draftRef.current === "") {
                draftRef.current = question;
                setDraft(question);
            }
            return false;
        } finally {
            if (activeRequest.current === requestId && mounted.current) {
                inFlight.current = false;
                controller.current = null;
                setBusy(false);
            }
        }
    };

    return { busy, error, send, clearError: () => setError(null) };
}
