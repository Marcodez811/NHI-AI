import { StrictMode, useState } from "react";
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatRequest } from "../lib/api/chat";
import type { ChatMessage } from "../lib/workspace/types";
import { useChatRequest } from "../lib/hooks/useChatRequest";

const streamChatMock = vi.hoisted(() => vi.fn());

vi.mock("../lib/api/chat", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../lib/api/chat")>()),
    streamChat: streamChatMock,
}));

const payload: ChatRequest = {
    question: "健保政策是什麼？",
    mode: "legislative_qa",
    document_ids: [],
};

function useHarness(initialChat: ChatMessage[] = []) {
    const [chat, setChat] = useState(initialChat);
    const [draft, setDraft] = useState(payload.question);
    const request = useChatRequest({ draft, setDraft, setChat });
    return { chat, draft, setDraft, request };
}

describe("useChatRequest", () => {
    beforeEach(() => {
        streamChatMock.mockReset();
    });

    it("targets one assistant message with immutable delta updates", async () => {
        streamChatMock.mockImplementation(async (_payload, handlers) => {
            handlers.onStatus?.("drafting");
            handlers.onDelta("第一段");
            handlers.onDelta("第二段");
            handlers.onDone?.([]);
        });
        const { result } = renderHook(() => useHarness(), {
            wrapper: StrictMode,
        });

        await act(async () => {
            await result.current.request.send(payload);
        });

        expect(result.current.chat).toHaveLength(2);
        expect(result.current.chat[0]).toMatchObject({ role: "user", text: payload.question });
        expect(result.current.chat[1]).toMatchObject({
            role: "assistant",
            text: "第一段第二段",
            status: undefined,
        });
        expect(result.current.chat[0].id).toBeTruthy();
        expect(result.current.chat[1].id).toBeTruthy();
    });

    it("keeps the user message and restores the question after failure", async () => {
        streamChatMock.mockRejectedValue(new Error("network"));
        const oldMessage: ChatMessage = { id: "old", role: "user", text: "舊問題" };
        const { result } = renderHook(() => useHarness([oldMessage]));

        await act(async () => {
            await result.current.request.send(payload);
        });

        expect(result.current.chat.map((message) => message.text)).toEqual([
            "舊問題",
            payload.question,
        ]);
        expect(result.current.draft).toBe(payload.question);
        expect(result.current.request.error).toBeTruthy();
        expect(result.current.request.busy).toBe(false);
    });
});
