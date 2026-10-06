import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pathname = vi.hoisted(() => ({ current: "/chat" }));
vi.mock("next/navigation", () => ({
    usePathname: () => pathname.current,
}));

const sessionsState = vi.hoisted(() => ({
    sessions: [
        { id: "s1", title: "健保給付問答", updated_at: "2026-01-02T00:00:00Z" },
        { id: "s2", title: "藥價調整討論", updated_at: "2026-01-01T00:00:00Z" },
    ],
    rename: vi.fn(),
    remove: vi.fn(),
}));
vi.mock("../lib/hooks/useChatSessions", () => ({
    useChatSessions: () => sessionsState,
}));

import { ChatSidebarSessions } from "../components/chat/ChatSidebarSessions";

describe("ChatSidebarSessions", () => {
    beforeEach(() => {
        // The suite's global `restoreMocks` config wipes vi.fn() implementations before
        // each test, including this hoisted setup, so the resolved value is reapplied here.
        sessionsState.rename.mockResolvedValue(undefined);
        sessionsState.remove.mockResolvedValue(undefined);
    });

    afterEach(() => {
        cleanup();
        vi.clearAllMocks();
        pathname.current = "/chat";
    });

    it("lists recent conversations as links to /chat/<id>, highlighting the current one", () => {
        pathname.current = "/chat/s2";
        render(<ChatSidebarSessions />);

        const first = screen.getByRole("link", { name: "健保給付問答" });
        expect(first).toHaveAttribute("href", "/chat/s1");
        const second = screen.getByRole("link", { name: "藥價調整討論" });
        expect(second).toHaveAttribute("href", "/chat/s2");
        expect(second).toHaveAttribute("aria-current", "page");
        expect(first).not.toHaveAttribute("aria-current");
    });

    it("renders nothing when there are no conversations yet", () => {
        sessionsState.sessions = [];
        const { container } = render(<ChatSidebarSessions />);
        expect(container).toBeEmptyDOMElement();
        sessionsState.sessions = [
            { id: "s1", title: "健保給付問答", updated_at: "2026-01-02T00:00:00Z" },
            { id: "s2", title: "藥價調整討論", updated_at: "2026-01-01T00:00:00Z" },
        ];
    });

    it("renames a conversation through the menu", async () => {
        const user = userEvent.setup();
        render(<ChatSidebarSessions />);

        await user.click(screen.getByRole("button", { name: "「健保給付問答」的更多操作" }));
        await user.click(await screen.findByRole("menuitem", { name: /重新命名/ }));

        const input = screen.getByRole("textbox", { name: "重新命名對話" });
        await user.clear(input);
        await user.type(input, "健保給付新標題{Enter}");

        expect(sessionsState.rename).toHaveBeenCalledWith("s1", "健保給付新標題");
    });

    it("deletes a conversation through the menu after confirming", async () => {
        const user = userEvent.setup();
        render(<ChatSidebarSessions />);

        await user.click(screen.getByRole("button", { name: "「藥價調整討論」的更多操作" }));
        await user.click(await screen.findByRole("menuitem", { name: /刪除/ }));

        expect(sessionsState.remove).not.toHaveBeenCalled();
        expect(await screen.findByText("此操作無法復原。")).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "刪除" }));

        expect(sessionsState.remove).toHaveBeenCalledWith("s2");
    });

    it("does not delete when the dialog is cancelled", async () => {
        const user = userEvent.setup();
        render(<ChatSidebarSessions />);

        await user.click(screen.getByRole("button", { name: "「藥價調整討論」的更多操作" }));
        await user.click(await screen.findByRole("menuitem", { name: /刪除/ }));
        await user.click(await screen.findByRole("button", { name: "取消" }));

        expect(sessionsState.remove).not.toHaveBeenCalled();
    });
});
