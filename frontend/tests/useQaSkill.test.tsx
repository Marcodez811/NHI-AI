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
    sendChatMessage: vi.fn(),
}));
vi.mock("../lib/api/chat", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../lib/api/chat")>()),
    ...api,
}));
const qaApi = vi.hoisted(() => ({
    putChatSkill: vi.fn(),
    fetchQaWorkspace: vi.fn(),
    confirmQaDocuments: vi.fn(),
    putQaQuestions: vi.fn(),
    putQaOutline: vi.fn(),
    confirmQaOutline: vi.fn(),
    startQaGeneration: vi.fn(),
    fetchQaGeneration: vi.fn(),
    startQaRevision: vi.fn(),
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

    describe("auto-continue after a confirm", () => {
        const setup = async () => {
            api.fetchChatSession.mockResolvedValue(detail({ skill: "legislative_qa" }));
            qaApi.fetchQaWorkspace.mockResolvedValue(workspace);
            const hook = renderHook(() => useChatSession("s1"));
            await waitFor(() => expect(hook.result.current.qa.skill).toBe("legislative_qa"));
            return hook;
        };

        it("sends a hidden continuation turn after confirming the questions", async () => {
            api.sendChatMessage.mockResolvedValue(undefined);
            qaApi.putQaQuestions.mockResolvedValue(workspace);
            const { result } = await setup();
            await act(async () => { await result.current.qa.saveQuestions([{ text: "題" }], true); });
            await waitFor(() => expect(api.sendChatMessage).toHaveBeenCalledTimes(1));
            expect(api.sendChatMessage.mock.calls[0][1]).toMatchObject({
                content: "使用者已在卡片上確認了題目清單，請進行下一個階段。",
                display_text: "✓ 已確認題目清單",
                attachment_ids: [],
            });
            expect(result.current.messages[0]).toMatchObject({ role: "user", display_text: "✓ 已確認題目清單" });
        });

        it("labels outline confirms and documents confirms", async () => {
            api.sendChatMessage.mockResolvedValue(undefined);
            qaApi.confirmQaOutline.mockResolvedValue(workspace);
            qaApi.confirmQaDocuments.mockResolvedValue(workspace);
            const { result } = await setup();
            await act(async () => { await result.current.qa.confirmOutline(2); });
            await waitFor(() => expect(api.sendChatMessage).toHaveBeenCalledTimes(1));
            expect(api.sendChatMessage.mock.calls[0][1].display_text).toBe("✓ 已確認第 2 題大綱");
            await act(async () => { await result.current.qa.confirmDocuments(); });
            await waitFor(() => expect(api.sendChatMessage).toHaveBeenCalledTimes(2));
            expect(api.sendChatMessage.mock.calls[1][1].display_text).toBe("✓ 已確認參考資料");
        });

        it("does not continue on edits and never starts a second turn while streaming", async () => {
            let finish: () => void = () => {};
            api.sendChatMessage.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
            qaApi.putQaQuestions.mockResolvedValue(workspace);
            qaApi.putQaOutline.mockResolvedValue(workspace);
            qaApi.confirmQaOutline.mockResolvedValue(workspace);
            const { result } = await setup();
            await act(async () => { await result.current.qa.saveQuestions([{ text: "題" }], false); });
            await act(async () => { await result.current.qa.saveOutline(1, { short: ["a"], detail: [], dispute_requested: false, confirm: false }); });
            expect(api.sendChatMessage).not.toHaveBeenCalled();
            await act(async () => { await result.current.qa.confirmOutline(1); });
            await waitFor(() => expect(api.sendChatMessage).toHaveBeenCalledTimes(1));
            await act(async () => { await result.current.qa.confirmOutline(2); });
            expect(api.sendChatMessage).toHaveBeenCalledTimes(1);
            await act(async () => { finish(); });
        });
    });

    it("starts the AI writer, follows its progress and lists the new AI version when it ends", async () => {
        vi.useFakeTimers({ shouldAdvanceTime: true });
        try {
            api.fetchChatSession.mockResolvedValue(detail({ skill: "legislative_qa" }));
            qaApi.fetchQaWorkspace.mockResolvedValue({ ...workspace, stage: "ready" });
            const { result } = renderHook(() => useChatSession("s1"));
            await waitFor(() => expect(result.current.qa.workspace?.stage).toBe("ready"));
            qaApi.startQaGeneration.mockResolvedValue({ job_id: "j1", status: "queued", phase: "queued" });
            qaApi.fetchQaWorkspace.mockResolvedValue({ ...workspace, stage: "generating", generation: { job_id: "j1" } });
            qaApi.fetchQaGeneration.mockResolvedValueOnce({ job_id: "j1", status: "running", phase: "drafting" });
            await act(async () => { await result.current.qa.startGeneration("11-5"); });
            expect(qaApi.startQaGeneration).toHaveBeenCalledWith("s1", "11-5");
            await waitFor(() => expect(result.current.qa.generation).toEqual({ running: true, phase: "drafting", error: null }));

            const versions = [{ kind: "ai", artifact_id: "a1", title: "11-5 立法院QA（1 題）", created_at: "", download_url: "/x" }];
            qaApi.fetchQaWorkspace.mockResolvedValue({ ...workspace, stage: "ready", versions });
            qaApi.fetchQaGeneration.mockResolvedValue({ job_id: "j1", status: "completed", phase: "completed", artifact_id: "a1" });
            await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
            await waitFor(() => expect(result.current.qa.workspace?.versions).toEqual(versions));
            expect(result.current.qa.generation).toEqual({ running: false, phase: null, error: null });
        } finally {
            vi.useRealTimers();
        }
    });

    it("resumes a running job after a refresh and reports a failure for retry", async () => {
        api.fetchChatSession.mockResolvedValue(detail({ skill: "legislative_qa" }));
        qaApi.fetchQaWorkspace
            .mockResolvedValueOnce({ ...workspace, stage: "generating", generation: { job_id: "j9" } })
            .mockResolvedValue({ ...workspace, stage: "ready" });
        qaApi.fetchQaGeneration.mockResolvedValue({ job_id: "j9", status: "failed", phase: "failed", error: "格式不正確" });
        const { result } = renderHook(() => useChatSession("s1"));
        await waitFor(() => expect(result.current.qa.generation.error).toBe("格式不正確"));
        expect(qaApi.fetchQaGeneration).toHaveBeenCalledWith("s1", "j9");
        expect(result.current.qa.generation.running).toBe(false);
    });

    it("starts a revision and moves an open preview to the finished revised version", async () => {
        api.fetchChatSession.mockResolvedValue(detail({ skill: "legislative_qa" }));
        const base = { kind: "ai", artifact_id: "a1", title: "原版", created_at: "", download_url: "/x" };
        qaApi.fetchQaWorkspace.mockResolvedValue({ ...workspace, stage: "ready", versions: [base] });
        const { result } = renderHook(() => useChatSession("s1"));
        await waitFor(() => expect(result.current.qa.workspace?.versions).toHaveLength(1));
        act(() => result.current.qa.openPreview("a1"));
        expect(result.current.qa.previewId).toBe("a1");

        qaApi.startQaRevision.mockResolvedValue({ job_id: "j2", status: "queued", phase: "queued" });
        qaApi.fetchQaWorkspace.mockResolvedValue({ ...workspace, stage: "generating", versions: [base], generation: { job_id: "j2" } });
        qaApi.fetchQaGeneration.mockResolvedValue({ job_id: "j2", status: "running", phase: "drafting" });
        const body = { question_no: 3, part: "short" as const, instruction: "肯定一點", base_artifact_id: "a1" };
        await act(async () => { await result.current.qa.reviseVersion(body); });
        expect(qaApi.startQaRevision).toHaveBeenCalledWith("s1", body);
        await waitFor(() => expect(result.current.qa.generation.running).toBe(true));
        expect(result.current.qa.previewId).toBe("a1");

        const revised = { ...base, artifact_id: "a2", title: "修訂版", revised_from: "a1", revision_note: "肯定一點" };
        qaApi.fetchQaWorkspace.mockResolvedValue({ ...workspace, stage: "ready", versions: [revised, base] });
        qaApi.fetchQaGeneration.mockResolvedValue({ job_id: "j2", status: "completed", phase: "completed", artifact_id: "a2" });
        await waitFor(() => expect(result.current.qa.previewId).toBe("a2"), { timeout: 4000 });
    });

    it("leaves a closed preview closed when a revision finishes", async () => {
        api.fetchChatSession.mockResolvedValue(detail({ skill: "legislative_qa" }));
        const base = { kind: "ai", artifact_id: "a1", title: "原版", created_at: "", download_url: "/x" };
        qaApi.fetchQaWorkspace.mockResolvedValue({ ...workspace, stage: "ready", versions: [base] });
        const { result } = renderHook(() => useChatSession("s1"));
        await waitFor(() => expect(result.current.qa.workspace?.versions).toHaveLength(1));
        qaApi.fetchQaWorkspace.mockResolvedValue({
            ...workspace, stage: "ready", versions: [{ ...base, artifact_id: "a2", revised_from: "a1" }, base],
        });
        await act(async () => { await result.current.qa.refresh(); });
        await waitFor(() => expect(result.current.qa.workspace?.versions).toHaveLength(2));
        expect(result.current.qa.previewId).toBeNull();
    });
});
