import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatPage } from "../components/chat/ChatPage";

const baseChat = {
    sessionId: null as string | null,
    messages: [] as Array<{ localKey: string; role: "user" | "assistant" }>,
    loading: false,
    loadError: null as string | null,
    compactedThroughMessageId: null as string | null,
    draft: "",
    setDraft: vi.fn(),
    attachments: [],
    attachFiles: vi.fn(),
    removeAttachment: vi.fn(),
    models: [],
    model: undefined,
    setModel: vi.fn(),
    modelsLoading: false,
    busy: false,
    uploadsPending: false,
    send: vi.fn(),
    stop: vi.fn(),
    sendError: null as string | null,
};

let chatOverrides: Record<string, unknown> = {};

vi.mock("../lib/hooks/useChatEngine", () => ({
    useChatEngine: () => ({ ...baseChat, ...chatOverrides }),
}));

vi.mock("../components/chat/ChatMessageList", () => ({
    ChatMessageList: () => <div data-testid="message-list" />,
}));

describe("ChatPage layout", () => {
    afterEach(() => {
        cleanup();
        chatOverrides = {};
    });

    it("centers the greeting and composer together on an empty conversation", () => {
        render(<ChatPage />);
        const centered = document.querySelector('[data-chat-layout="centered"]');
        expect(centered).toBeTruthy();
        expect(centered).toHaveTextContent("健保署 AI 助理");
        expect(centered?.querySelector("textarea")).toBeTruthy();
        expect(document.querySelector('[data-chat-layout="docked"]')).toBeFalsy();
    });

    it("docks the composer at the bottom once messages exist", () => {
        chatOverrides = { messages: [{ localKey: "1", role: "user" }] };
        render(<ChatPage />);
        const docked = document.querySelector('[data-chat-layout="docked"]');
        expect(docked).toBeTruthy();
        expect(docked?.querySelector("textarea")).toBeTruthy();
        expect(screen.getByTestId("message-list")).toBeInTheDocument();
        expect(document.querySelector('[data-chat-layout="centered"]')).toBeFalsy();
    });
});
