import { act, renderHook } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { DEFAULT_CLIENT_CONFIG } from "../lib/api/config";
import { useChatSession } from "../lib/hooks/useChatSession";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));
vi.mock("../lib/hooks/useChatSessions", () => ({
    useChatSessions: () => ({ sessions: [], loading: false, error: null, refresh: vi.fn(), rename: vi.fn(), remove: vi.fn() }),
}));
vi.mock("../lib/hooks/useClientConfig", () => ({
    useClientConfig: () => ({
        ...DEFAULT_CLIENT_CONFIG,
        chat: { ...DEFAULT_CLIENT_CONFIG.chat, max_attachments: 1, max_document_bytes: 5 },
    }),
}));
const api = vi.hoisted(() => ({ createChatSession: vi.fn(), uploadChatAttachment: vi.fn() }));
vi.mock("../lib/api/chat", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../lib/api/chat")>()),
    createChatSession: api.createChatSession,
    uploadChatAttachment: api.uploadChatAttachment,
}));

it("validates attachments against the limits from the server config", async () => {
    api.createChatSession.mockResolvedValue({ id: "s1" });
    const { result } = renderHook(() => useChatSession(null));
    const big = new File(["0123456789"], "big.txt", { type: "text/plain" });
    const extra = new File(["x"], "extra.txt", { type: "text/plain" });
    await act(async () => {
        await result.current.attachFiles([big, extra]);
    });
    // Only one slot is allowed, and the oversized file fails client-side without uploading.
    expect(result.current.attachments).toHaveLength(1);
    expect(result.current.attachments[0].status).toBe("failed");
    expect(api.uploadChatAttachment).not.toHaveBeenCalled();
});
