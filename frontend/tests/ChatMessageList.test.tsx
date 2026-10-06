import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ChatMessageList } from "../components/chat/ChatMessageList";
import type { ChatTurn } from "../lib/hooks/useChatSession";

function assistant(overrides: Partial<ChatTurn> = {}): ChatTurn {
    return {
        id: "assistant-id", localKey: "assistant-local", role: "assistant", content: "",
        status: "complete", created_at: "2026-09-29T00:00:00Z", ...overrides,
    };
}

function user(overrides: Partial<ChatTurn> = {}): ChatTurn {
    return {
        id: "user-id", localKey: "user-local", role: "user", content: "你好",
        status: "complete", created_at: "2026-09-29T00:00:00Z", ...overrides,
    };
}

describe("ChatMessageList", () => {
    afterEach(() => cleanup());

    it("renders the user bubble as a light, neutral tint rather than the solid primary color", () => {
        render(<ChatMessageList messages={[user()]} />);
        const bubble = screen.getByText("你好").parentElement!;
        expect(bubble.className).not.toMatch(/(^|\s)bg-primary(?!\/)/);
        expect(bubble.className).not.toMatch(/text-primary-foreground/);
    });

    it("shows attachment chips above a user message, with a knowledge-base badge, skipping unknown ids", () => {
        const lookup = {
            a1: { id: "a1", display_name: "報告.pdf", mime_type: "application/pdf", kind: "document", size_bytes: 1, status: "ready", error: null, text_chars: 1, created_at: "", source: "upload" },
            a2: { id: "a2", display_name: "給付規則.docx", mime_type: "x", kind: "document", size_bytes: 1, status: "ready", error: null, text_chars: 1, created_at: "", source: "knowledge_base" },
        } as const;
        render(<ChatMessageList sessionId="s1" attachmentLookup={lookup} messages={[user({ attachment_ids: ["a1", "a2", "gone"] })]} />);
        expect(screen.getByText("報告.pdf")).toBeInTheDocument();
        expect(screen.getByText("給付規則.docx")).toBeInTheDocument();
        expect(screen.getAllByText("知識庫")).toHaveLength(1);
        expect(screen.queryByText("gone")).not.toBeInTheDocument();
    });

    it("shows thinking before the reply, compaction while summarizing, then a live answer caret", () => {
        const view = render(<ChatMessageList messages={[assistant({ pending: true })]} />);
        expect(screen.getByText("思考中…")).toHaveClass("chat-shimmer");
        view.rerender(<ChatMessageList messages={[assistant({ pending: true, compacting: true })]} />);
        expect(screen.queryByText("思考中…")).not.toBeInTheDocument();
        expect(screen.getByText("整理先前對話中…")).toHaveClass("chat-shimmer");
        view.rerender(<ChatMessageList messages={[assistant({ pending: true, content: "已確認" })]} />);
        expect(view.container.querySelector(".chat-streaming-answer")).toBeInTheDocument();
        view.rerender(<ChatMessageList messages={[assistant({ content: "已確認" })]} />);
        expect(view.container.querySelector(".chat-streaming-answer")).not.toBeInTheDocument();
    });

    it("renders reasoning as Markdown, not raw asterisks", () => {
        render(<ChatMessageList messages={[assistant({ content: "回答", reasoning: "**確認問題**\n\n先查詢知識庫" })]} />);
        const block = screen.getByText("思考過程").closest("details")!;
        expect(within(block).getByText("確認問題")).toBeInTheDocument();
        expect(block.textContent).not.toContain("**");
    });

    it("opens live reasoning and collapses it on first answer; stored reasoning starts collapsed", () => {
        const view = render(<ChatMessageList messages={[assistant({ pending: true, reasoning: "檢視資料", reasoningStreaming: true })]} />);
        const block = screen.getByText("思考過程").closest("details")!;
        expect(block.open).toBe(true);
        view.rerender(<ChatMessageList messages={[assistant({ pending: true, content: "回答", answerStarted: true, reasoning: "檢視資料", reasoningStreaming: false, reasoningDurationSeconds: 3 })]} />);
        expect(block.open).toBe(false);
        expect(within(block).getByText("思考了 3 秒")).toBeInTheDocument();
        fireEvent.click(within(block).getByText("思考了 3 秒"));
        expect(block.open).toBe(true);
        view.unmount();
        render(<ChatMessageList messages={[assistant({ content: "回答", reasoning: "先前推理" })]} />);
        expect(screen.getByText("思考過程").closest("details")!.open).toBe(false);
    });

    it("collapses tools when answer starts or turn ends and counts each mapped tool once per call", () => {
        const toolSteps = [
            { tool: "search_knowledge_base", label: "搜尋知識庫：藥費", done: true },
            { tool: "search_knowledge_base", label: "找到 6 筆資料", done: true },
            { tool: "read_attachment", label: "讀取附件：公文.pdf", done: false },
            { tool: "open_attachment", label: "重新開啟附件：公文.pdf", done: true },
            { tool: "future_tool", label: "已完成", done: true },
        ];
        const view = render(<ChatMessageList messages={[assistant({ pending: true, toolSteps })]} />);
        expect(screen.getByLabelText("工具使用進度")).toBeInTheDocument();
        expect(screen.getByText("讀取附件：公文.pdf")).toHaveClass("chat-shimmer");
        view.rerender(<ChatMessageList messages={[assistant({ pending: true, answerStarted: true, content: "結論", toolSteps })]} />);
        const summary = screen.getByText("已搜尋知識庫 2 次・已讀取附件 1 個・已重新開啟附件 1 個・已使用工具 1 次");
        const details = summary.closest("details")!;
        expect(details.open).toBe(false);
        fireEvent.click(summary);
        expect(details.open).toBe(true);
        expect(within(details).getByText("搜尋知識庫：藥費")).toBeInTheDocument();
        view.unmount();
        render(<ChatMessageList messages={[assistant({ toolSteps })]} />);
        expect(screen.getByText("已搜尋知識庫 2 次・已讀取附件 1 個・已重新開啟附件 1 個・已使用工具 1 次").closest("details")!.open).toBe(false);
    });

    it("places the compaction divider after the matching message only", () => {
        const user: ChatTurn = { id: "old-message", localKey: "user", role: "user", content: "舊問題", status: "complete", created_at: "2026-09-29T00:00:00Z" };
        const view = render(<ChatMessageList messages={[user, assistant({ content: "新答案" })]} compactedThroughMessageId="old-message" />);
        const marker = screen.getByRole("separator", { name: "已摘要較早的對話" });
        expect(marker.previousElementSibling).toHaveTextContent("舊問題");
        expect(marker.nextElementSibling).toHaveTextContent("新答案");
        view.rerender(<ChatMessageList messages={[user, assistant({ content: "新答案" })]} compactedThroughMessageId="missing" />);
        expect(screen.queryByRole("separator")).not.toBeInTheDocument();
    });
});
