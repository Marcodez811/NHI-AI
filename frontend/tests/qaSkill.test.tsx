import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatComposer } from "../components/chat/ChatComposer";
import { ChatMessageList } from "../components/chat/ChatMessageList";
import { QaStepper } from "../components/chat/qa/QaStepper";
import type { QaController } from "../components/chat/qa/QaCards";
import { parseChatEventBlock } from "../lib/api/chat";
import type { QaCard, QaWorkspace } from "../lib/api/qa";
import type { ChatTurn } from "../lib/hooks/useChatSession";

afterEach(() => cleanup());

function workspace(overrides: Partial<QaWorkspace> = {}): QaWorkspace {
    return {
        stage: "questions",
        questions: { items: [{ no: 1, text: "請說明A", note: "備註" }], confirmed: false },
        documents: { confirmed: false, used: [] },
        evidence: {},
        outline: {},
        ...overrides,
    };
}

function controller(overrides: Partial<QaController> = {}, ws: QaWorkspace | null = workspace()): QaController {
    return {
        skill: "legislative_qa",
        workspace: ws,
        sessionAttachments: [],
        reset: vi.fn(), hydrate: vi.fn(), refresh: vi.fn(), enable: vi.fn(), disable: vi.fn(),
        saveQuestions: vi.fn().mockResolvedValue(undefined),
        saveOutline: vi.fn().mockResolvedValue(undefined),
        confirmOutline: vi.fn().mockResolvedValue(undefined),
        confirmAllOutlines: vi.fn().mockResolvedValue(undefined),
        confirmDocuments: vi.fn().mockResolvedValue(undefined),
        toggleDocument: vi.fn().mockResolvedValue(undefined),
        ...overrides,
    } as QaController;
}

function turn(cards: QaCard[], key = "a1"): ChatTurn {
    return {
        id: key, localKey: key, role: "assistant", content: "好的", status: "complete",
        created_at: "2026-10-08T00:00:00Z", cards,
    };
}

const outline = (confirmed = false) => ({
    short: ["要點一"], detail: [{ title: "背景", points: ["說明一"] }], dispute_requested: false, confirmed,
});

describe("composer skill entry", () => {
    function renderComposer(props: Partial<React.ComponentProps<typeof ChatComposer>> = {}) {
        render(<ChatComposer sessionId={null} draft="" setDraft={vi.fn()} attachments={[]} onAttachFiles={vi.fn()}
            onRemoveAttachment={vi.fn()} models={[]} model={undefined} setModel={vi.fn()} modelsLoading={false}
            busy={false} uploadsPending={false} onSend={vi.fn()} onStop={vi.fn()} {...props} />);
    }

    it("lists 立院QA in the plus menu and selects it", async () => {
        const onSelectSkill = vi.fn();
        renderComposer({ onSelectSkill });
        await userEvent.click(screen.getByRole("button", { name: "附加檔案" }));
        await userEvent.click(await screen.findByText("立院QA（立法院質詢答題）"));
        expect(onSelectSkill).toHaveBeenCalled();
    });

    it("shows the chip and confirms before leaving the mode", async () => {
        const onExitSkill = vi.fn();
        renderComposer({ skill: "legislative_qa", onExitSkill });
        expect(screen.getByText("立院QA")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "離開立院QA模式" }));
        expect(onExitSkill).not.toHaveBeenCalled();
        expect(await screen.findByText("離開立院QA模式？")).toBeInTheDocument();
        expect(screen.getByText("工作區內容會保留。")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "離開" }));
        expect(onExitSkill).toHaveBeenCalled();
    });
});

describe("QaStepper", () => {
    it("marks finished steps and highlights the current one", () => {
        render(<QaStepper stage="evidence" />);
        expect(screen.getByRole("button", { name: "題目" })).toHaveAttribute("data-state", "done");
        expect(screen.getByRole("button", { name: "資料" })).toHaveAttribute("data-state", "done");
        expect(screen.getByText("整理")).toHaveAttribute("data-state", "current");
        expect(screen.getByText("架構")).toHaveAttribute("data-state", "todo");
    });
});

describe("skill_card SSE", () => {
    it("parses a skill_card event", () => {
        const event = parseChatEventBlock('data: {"type":"skill_card","kind":"questions","data":{"items":[],"confirmed":false}}');
        expect(event).toEqual({ type: "skill_card", card: { kind: "questions", data: { items: [], confirmed: false } } });
    });
    it("rejects an unknown card kind", () => {
        expect(() => parseChatEventBlock('data: {"type":"skill_card","kind":"nope","data":{}}')).toThrow();
    });
});

describe("question card", () => {
    const card: QaCard = { kind: "questions", data: { items: [{ no: 1, text: "請說明A", note: "備註" }], confirmed: false } };

    it("confirms with the current items", async () => {
        const qa = controller();
        render(<ChatMessageList messages={[turn([card])]} qa={qa} />);
        expect(screen.getByText("請說明A")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "確認" }));
        expect(qa.saveQuestions).toHaveBeenCalledWith([{ no: 1, text: "請說明A", note: "備註" }], true);
    });

    it("edits a question and saves without confirming", async () => {
        const qa = controller();
        render(<ChatMessageList messages={[turn([card])]} qa={qa} />);
        await userEvent.click(screen.getByRole("button", { name: "編輯" }));
        const input = screen.getByLabelText("第 1 題題目");
        await userEvent.clear(input);
        await userEvent.type(input, "改過的題目");
        await userEvent.click(screen.getByRole("button", { name: "儲存" }));
        expect(qa.saveQuestions).toHaveBeenCalledWith([{ no: 1, text: "改過的題目", note: "備註" }], false);
    });

    it("shows the confirmed badge from the live workspace and disables confirm", () => {
        const qa = controller({}, workspace({ questions: { items: [{ no: 1, text: "請說明A" }], confirmed: true } }));
        render(<ChatMessageList messages={[turn([card])]} qa={qa} />);
        expect(screen.getByText("已確認")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "確認" })).toBeDisabled();
    });

    it("shows the server message inline when an action fails", async () => {
        const { ApiError } = await import("../lib/api/client");
        const qa = controller({ saveQuestions: vi.fn().mockRejectedValue(new ApiError(422, "題目不可為空")) });
        render(<ChatMessageList messages={[turn([card])]} qa={qa} />);
        await userEvent.click(screen.getByRole("button", { name: "確認" }));
        expect(await screen.findByRole("alert")).toHaveTextContent("題目不可為空");
    });

    it("renders only the newest snapshot as actionable", () => {
        const qa = controller();
        render(<ChatMessageList messages={[turn([card], "a1"), turn([card], "a2")]} qa={qa} />);
        expect(screen.getAllByRole("button", { name: "確認" })).toHaveLength(1);
        expect(screen.getByText("題目清單（較早版本）")).toBeInTheDocument();
    });
});

describe("documents card", () => {
    const card: QaCard = {
        kind: "documents",
        data: {
            confirmed: false,
            items: [{
                question_no: 1, question_text: "請說明A",
                candidates: [
                    { id: "doc-1", name: "資料甲.pdf", snippet: "摘要甲", attached: false },
                    { id: "doc-2", name: "資料乙.pdf", snippet: "摘要乙", attached: true },
                ],
            }],
        },
    };
    const attachment = (id: string, name: string) => ({
        id, display_name: name, mime_type: "application/pdf", kind: "document" as const, size_bytes: 1,
        status: "ready" as const, error: null, text_chars: 1, created_at: "", source: "knowledge_base" as const,
    });

    it("ticks a candidate through the link call and confirms the kept attachments", async () => {
        const qa = controller({ sessionAttachments: [attachment("doc-2", "資料乙.pdf")] });
        render(<ChatMessageList messages={[turn([card])]} qa={qa} />);
        expect(screen.getByRole("checkbox", { name: "資料乙.pdf" })).toBeChecked();
        await userEvent.click(screen.getByRole("checkbox", { name: "資料甲.pdf" }));
        expect(qa.toggleDocument).toHaveBeenCalledWith("doc-1", true);
        await userEvent.click(screen.getByRole("button", { name: "確認使用這些資料" }));
        expect(qa.confirmDocuments).toHaveBeenCalled();
    });
});

describe("evidence card", () => {
    it("renders the five groups with source chips and the quote", () => {
        const item = { text: "預算 10 億", source: { id: "s1", name: "報告.pdf" }, quote: "原文段落" };
        const card: QaCard = {
            kind: "evidence",
            data: { question_no: 1, question_text: "請說明A", evidence: { figures: [item], aim: [], status: [], dispute: [], next_steps: [] } },
        };
        render(<ChatMessageList messages={[turn([card])]} qa={controller()} />);
        for (const label of ["數據", "政策目的", "辦理現況", "爭議", "後續工作"]) expect(screen.getByText(label)).toBeInTheDocument();
        expect(screen.getByText("預算 10 億")).toBeInTheDocument();
        expect(screen.getByText("報告.pdf")).toBeInTheDocument();
        expect(screen.getByText("原文段落")).toBeInTheDocument();
    });
});

describe("outline card", () => {
    const card = (n: number): QaCard => ({
        kind: "outline", data: { question_no: n, question_text: `題${n}`, outline: outline() },
    });

    it("confirms one outline", async () => {
        const qa = controller();
        render(<ChatMessageList messages={[turn([card(1)])]} qa={qa} />);
        expect(screen.getByText("要點一")).toBeInTheDocument();
        expect(screen.getByText("說明一")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "確認" }));
        expect(qa.confirmOutline).toHaveBeenCalledWith(1);
    });

    it("edits an outline with the dispute toggle", async () => {
        const qa = controller();
        render(<ChatMessageList messages={[turn([card(1)])]} qa={qa} />);
        await userEvent.click(screen.getByRole("button", { name: "修改" }));
        await userEvent.click(screen.getByRole("checkbox", { name: "加入爭議點" }));
        await userEvent.click(screen.getByRole("button", { name: "儲存" }));
        await waitFor(() => expect(qa.saveOutline).toHaveBeenCalledWith(1, {
            short: ["要點一"], detail: [{ title: "背景", points: ["說明一"] }], dispute_requested: true, confirm: false,
        }));
    });

    it("offers 全部確認 once every question has an outline", async () => {
        const ws = workspace({ stage: "outline", outline: { "1": outline() } });
        const qa = controller({}, ws);
        render(<ChatMessageList messages={[turn([card(1)])]} qa={qa} />);
        await userEvent.click(screen.getByRole("button", { name: "全部確認" }));
        expect(qa.confirmAllOutlines).toHaveBeenCalled();
    });

    it("notes that generation is coming once the stage is ready", () => {
        const ws = workspace({ stage: "ready", outline: { "1": outline(true) } });
        render(<ChatMessageList messages={[turn([card(1)])]} qa={controller({}, ws)} />);
        expect(screen.getByText("大綱已全部確認，產製功能即將開放。")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "全部確認" })).not.toBeInTheDocument();
    });
});
