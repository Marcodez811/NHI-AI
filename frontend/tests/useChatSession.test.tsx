import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChatSession } from "../lib/hooks/useChatSession";
import type { ChatStreamHandlers } from "../lib/api/chat";

const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({
    useRouter: () => router,
}));

const sessionsRefresh = vi.hoisted(() => vi.fn());
vi.mock("../lib/hooks/useChatSessions", () => ({
    useChatSessions: () => ({
        sessions: [],
        loading: false,
        error: null,
        refresh: sessionsRefresh,
        rename: vi.fn(),
        remove: vi.fn(),
    }),
}));

const api = vi.hoisted(() => ({
    createChatSession: vi.fn(),
    fetchChatSession: vi.fn(),
    sendChatMessage: vi.fn(),
}));
vi.mock("../lib/api/chat", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../lib/api/chat")>()),
    createChatSession: api.createChatSession,
    fetchChatSession: api.fetchChatSession,
    sendChatMessage: api.sendChatMessage,
}));

describe("useChatSession", () => {
    afterEach(() => {
        vi.clearAllMocks();
        vi.restoreAllMocks();
    });

    it("creates a session on the first send and navigates to /chat/<id>", async () => {
        api.createChatSession.mockResolvedValue({
            id: "new-session",
            title: "新對話",
            model: "gpt-6-astra",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
            messages: [],
            attachments: [],
        });
        api.sendChatMessage.mockImplementation(async (_id, _payload, handlers) => {
            handlers.onMessageStart?.("m1");
            handlers.onDelta("你好");
            handlers.onDone?.("m1", "健保問答");
        });

        const { result } = renderHook(() => useChatSession(null, "gpt-6-astra"));
        await act(async () => undefined); // flush the default-model effect

        act(() => {
            result.current.setDraft("健保給付範圍是什麼？");
        });
        await act(async () => {
            await result.current.send();
        });

        expect(api.createChatSession).toHaveBeenCalledWith("gpt-6-astra");
        expect(router.replace).toHaveBeenCalledWith("/chat/new-session");
        expect(sessionsRefresh).toHaveBeenCalled();
        expect(result.current.messages.map((message) => message.role)).toEqual(["user", "assistant"]);
        expect(result.current.messages[0].content).toBe("健保給付範圍是什麼？");
        expect(result.current.messages[1].content).toBe("你好");
        expect(result.current.busy).toBe(false);
    });

    it("loads an existing session's history instead of creating one", async () => {
        api.fetchChatSession.mockResolvedValue({
            id: "s1",
            title: "既有對話",
            model: "gpt-6-astra",
            compacted_through_message_id: "m0",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
            messages: [
                { id: "m0", role: "user", content: "舊問題", status: "complete", created_at: "2026-01-01T00:00:00Z" },
                { id: "m1", role: "assistant", content: "舊答案", reasoning: "查證後回答", status: "complete", created_at: "2026-01-01T00:00:01Z" },
            ],
            attachments: [],
        });

        const { result } = renderHook(() => useChatSession("s1"));

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.messages).toHaveLength(2);
        expect(result.current.messages[0].content).toBe("舊問題");
        expect(result.current.messages[1]).toMatchObject({ reasoning: "查證後回答", reasoningStreaming: false });
        expect(result.current.compactedThroughMessageId).toBe("m0");
        expect(api.createChatSession).not.toHaveBeenCalled();
    });

    it("streams reasoning, freezes its duration when the answer begins, and ends the turn", async () => {
        api.createChatSession.mockResolvedValue({
            id: "new-session", title: "新對話", model: "gpt-6-astra", messages: [], attachments: [],
        });
        let handlers!: ChatStreamHandlers;
        let resolveStream!: () => void;
        api.sendChatMessage.mockImplementation((_id, _payload, callbacks) => {
            handlers = callbacks;
            return new Promise<void>((resolve) => { resolveStream = resolve; });
        });
        const now = vi.spyOn(Date, "now").mockReturnValue(1000);
        const { result } = renderHook(() => useChatSession(null, "gpt-6-astra"));
        act(() => result.current.setDraft("請說明給付規定"));
        let sending!: Promise<void>;
        act(() => { sending = result.current.send(); });
        await waitFor(() => expect(api.sendChatMessage).toHaveBeenCalled());
        expect(result.current.messages[1]).toMatchObject({
            pending: true, receivedEvent: false, answerStarted: false, compacting: false,
        });

        act(() => handlers.onMessageStart?.("m1"));
        expect(result.current.messages[1].receivedEvent).toBe(true);
        act(() => handlers.onCompacting?.());
        expect(result.current.messages[1].compacting).toBe(true);
        act(() => handlers.onCompacted?.(false));
        expect(result.current.messages[1].compacting).toBe(false);
        act(() => handlers.onReasoningDelta?.("先核對"));
        expect(result.current.messages[1]).toMatchObject({
            reasoning: "先核對", reasoningStartedAt: 1000, reasoningStreaming: true, answerStarted: false,
        });
        now.mockReturnValue(4000);
        act(() => handlers.onReasoningDelta?.("文件"));
        expect(result.current.messages[1].reasoning).toBe("先核對文件");
        act(() => handlers.onDelta("答覆"));
        expect(result.current.messages[1]).toMatchObject({
            reasoningStreaming: false, reasoningDurationSeconds: 3, answerStarted: true, content: "答覆",
        });
        now.mockReturnValue(9000);
        act(() => handlers.onDone?.("m1", "給付規定"));
        await act(async () => { resolveStream(); await sending; });
        expect(result.current.messages[1]).toMatchObject({
            pending: false, reasoningDurationSeconds: 3, status: "complete",
        });
    });

    it("stops reasoning and compaction on interruption", async () => {
        api.createChatSession.mockResolvedValue({
            id: "new-session", title: "新對話", model: "gpt-6-astra", messages: [], attachments: [],
        });
        let handlers!: ChatStreamHandlers;
        let rejectStream!: (error: Error) => void;
        api.sendChatMessage.mockImplementation((_id, _payload, callbacks) => {
            handlers = callbacks;
            return new Promise<void>((_resolve, reject) => { rejectStream = reject; });
        });
        const now = vi.spyOn(Date, "now").mockReturnValue(1000);
        const { result } = renderHook(() => useChatSession(null, "gpt-6-astra"));
        act(() => result.current.setDraft("請說明"));
        let sending!: Promise<void>;
        act(() => { sending = result.current.send(); });
        await waitFor(() => expect(api.sendChatMessage).toHaveBeenCalled());
        act(() => {
            handlers.onReasoningDelta?.("查詢中");
            handlers.onCompacting?.();
        });
        now.mockReturnValue(3000);
        act(() => result.current.stop());
        await act(async () => { rejectStream(new Error("aborted")); await sending; });
        expect(result.current.messages[1]).toMatchObject({
            pending: false, reasoningStreaming: false, reasoningDurationSeconds: 2,
            compacting: false, status: "interrupted",
        });
    });
});
