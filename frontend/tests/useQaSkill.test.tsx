import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChatSession } from "../lib/hooks/useChatSession";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));
vi.mock("../lib/hooks/useChatSessions", () => ({
    useChatSessions: () => ({ sessions: [], loading: false, error: null, refresh: vi.fn(), rename: vi.fn(), remove: vi.fn() }),
}));

const api = vi.hoisted(() => ({
    createChatSession: vi.fn(),
    fetchChatSession: vi.fn(),
    linkChatDocuments: vi.fn(),
    unlinkChatDocument: vi.fn(),
}));
vi.mock("../lib/api/chat", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../lib/api/chat")>()),
    ...api,
}));
const qaApi = vi.hoisted(() => ({
    putChatSkill: vi.fn(),
    fetchQaWorkspace: vi.fn(),
    confirmQaDocuments: vi.fn(),
}));
vi.mock("../lib/api/qa", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../lib/api/qa")>()),
    ...qaApi,
}));

const workspace = { stage: "questions", questions: { items: [], confirmed: false }, documents: { confirmed: false, used: [] }, evidence: {}, outline: {} };
const detail = (extra: object = {}) => ({
    id: "s1", title: "t", model: "m", created_at: "", updated_at: "", compacted_through_message_id: null,
    messages: [], attachments: [], skill: null, ...extra,
});

describe("useChatSession skill mode", () => {
    afterEach(() => vi.clearAllMocks());

    it("restores the mode and persisted cards from a reloaded session", async () => {
        api.fetchChatSession.mockResolvedValue(detail({
            skill: "legislative_qa",
            messages: [{ id: "m1", role: "assistant", content: "好", status: "complete", created_at: "",
                cards: [{ kind: "questions", data: { items: [], confirmed: false } }, { kind: "bogus", data: {} }] }],
        }));
        qaApi.fetchQaWorkspace.mockResolvedValue(workspace);
        const { result } = renderHook(() => useChatSession("s1"));
        await waitFor(() => expect(result.current.qa.skill).toBe("legislative_qa"));
        expect(result.current.messages[0].cards).toHaveLength(1);
        await waitFor(() => expect(result.current.qa.workspace?.stage).toBe("questions"));
    });

    it("enables the skill on a fresh chat by creating the session then PUT skill", async () => {
        api.createChatSession.mockResolvedValue(detail({ id: "new" }));
        api.fetchChatSession.mockResolvedValue(detail({ id: "new", skill: "legislative_qa" }));
        qaApi.putChatSkill.mockResolvedValue({ id: "new", skill: "legislative_qa" });
        qaApi.fetchQaWorkspace.mockResolvedValue(workspace);
        const { result } = renderHook(() => useChatSession(null));
        await act(async () => { await result.current.qa.enable(); });
        expect(qaApi.putChatSkill).toHaveBeenCalledWith("new", "legislative_qa");
        expect(result.current.qa.skill).toBe("legislative_qa");
    });

    it("links a ticked document and posts attachment ids on confirm", async () => {
        api.fetchChatSession.mockResolvedValue(detail({ skill: "legislative_qa" }));
        qaApi.fetchQaWorkspace.mockResolvedValue(workspace);
        qaApi.confirmQaDocuments.mockResolvedValue({ ...workspace, stage: "evidence" });
        const { result } = renderHook(() => useChatSession("s1"));
        await waitFor(() => expect(result.current.qa.skill).toBe("legislative_qa"));
        api.fetchChatSession.mockResolvedValue(detail({ skill: "legislative_qa", attachments: [{ id: "doc-1", display_name: "甲.pdf" }] }));
        await act(async () => { await result.current.qa.toggleDocument("doc-1", true); });
        expect(api.linkChatDocuments).toHaveBeenCalledWith("s1", ["doc-1"]);
        await act(async () => { await result.current.qa.confirmDocuments(); });
        expect(qaApi.confirmQaDocuments).toHaveBeenCalledWith("s1", ["doc-1"]);
    });

    it("clears the mode with PUT skill null", async () => {
        api.fetchChatSession.mockResolvedValue(detail({ skill: "legislative_qa" }));
        qaApi.fetchQaWorkspace.mockResolvedValue(workspace);
        qaApi.putChatSkill.mockResolvedValue({});
        const { result } = renderHook(() => useChatSession("s1"));
        await waitFor(() => expect(result.current.qa.skill).toBe("legislative_qa"));
        await act(async () => { await result.current.qa.disable(); });
        expect(qaApi.putChatSkill).toHaveBeenCalledWith("s1", null);
        expect(result.current.qa.skill).toBeNull();
    });
});
