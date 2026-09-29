import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChatSession } from "../lib/hooks/useChatSession";

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
    afterEach(() => vi.clearAllMocks());

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
            created_at: "2026-01-01T00:00:00Z",
            updated_at: "2026-01-01T00:00:00Z",
            messages: [
                { id: "m0", role: "user", content: "舊問題", status: "complete", created_at: "2026-01-01T00:00:00Z" },
            ],
            attachments: [],
        });

        const { result } = renderHook(() => useChatSession("s1"));

        await waitFor(() => expect(result.current.loading).toBe(false));
        expect(result.current.messages).toHaveLength(1);
        expect(result.current.messages[0].content).toBe("舊問題");
        expect(api.createChatSession).not.toHaveBeenCalled();
    });
});
