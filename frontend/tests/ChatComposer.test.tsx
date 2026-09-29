import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatComposer } from "../components/chat/ChatComposer";
import type { ComposerAttachment } from "../lib/hooks/useChatSession";
import type { ChatModelOption } from "../lib/api/chat";

const models: ChatModelOption[] = [
    { id: "gpt-6-astra", label: "GPT-6 Astra", provider: "openai", available: true },
];

function renderComposer(overrides: Partial<React.ComponentProps<typeof ChatComposer>> = {}) {
    const onSend = vi.fn();
    const onStop = vi.fn();
    const props: React.ComponentProps<typeof ChatComposer> = {
        sessionId: null,
        draft: "你好",
        setDraft: vi.fn(),
        attachments: [],
        onAttachFiles: vi.fn(),
        onRemoveAttachment: vi.fn(),
        models,
        model: "gpt-6-astra",
        setModel: vi.fn(),
        modelsLoading: false,
        busy: false,
        uploadsPending: false,
        onSend,
        onStop,
        ...overrides,
    };
    render(<ChatComposer {...props} />);
    return { onSend, onStop };
}

describe("ChatComposer", () => {
    afterEach(() => cleanup());

    it("blocks send while an attachment is still uploading", async () => {
        const uploading: ComposerAttachment = {
            localId: "a1",
            file: new File(["x"], "報告.pdf", { type: "application/pdf" }),
            status: "uploading",
        };
        const { onSend } = renderComposer({ attachments: [uploading], uploadsPending: true });

        const button = screen.getByRole("button", { name: "送出" });
        expect(button).toBeDisabled();

        await userEvent.click(button);
        expect(onSend).not.toHaveBeenCalled();
    });

    it("allows send once every attachment has finished uploading", async () => {
        const ready: ComposerAttachment = {
            localId: "a1",
            file: new File(["x"], "報告.pdf", { type: "application/pdf" }),
            status: "ready",
            attachment: {
                id: "att-1",
                display_name: "報告.pdf",
                mime_type: "application/pdf",
                kind: "document",
                size_bytes: 10,
                status: "ready",
                error: null,
                text_chars: 100,
                created_at: "2026-01-01T00:00:00Z",
            },
        };
        const { onSend } = renderComposer({ attachments: [ready], uploadsPending: false });

        const button = screen.getByRole("button", { name: "送出" });
        expect(button).toBeEnabled();
        await userEvent.click(button);
        expect(onSend).toHaveBeenCalledOnce();
    });

    it("disables send with an empty draft even without attachments", () => {
        renderComposer({ draft: "" });
        expect(screen.getByRole("button", { name: "送出" })).toBeDisabled();
    });

    it("shows Stop instead of Send while a reply is streaming, and Stop stays enabled", async () => {
        const { onStop } = renderComposer({ busy: true, uploadsPending: true });
        const button = screen.getByRole("button", { name: "停止" });
        expect(button).toBeEnabled();
        await userEvent.click(button);
        expect(onStop).toHaveBeenCalledOnce();
    });

    it("uses the new placeholder and moves the attachment note onto the attach button", () => {
        renderComposer();
        expect(screen.getByPlaceholderText("問問健保署 AI…")).toBeInTheDocument();

        const attachButton = screen.getByRole("button", { name: "附加檔案" });
        expect(attachButton).toHaveAttribute("title", "附件僅供此對話使用，不會加入知識庫");

        expect(screen.queryByText("附件僅供此對話使用，不會加入知識庫。")).not.toBeInTheDocument();
    });
});
