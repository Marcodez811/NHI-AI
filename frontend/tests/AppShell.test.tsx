import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/chat" }));

import { AppShell } from "../components/app-shell/app-shell";

describe("AppShell sidebar preference", () => {
    afterEach(() => {
        cleanup();
        window.localStorage.clear();
    });

    it("restores the saved preference before persisting", async () => {
        window.localStorage.setItem("nhi-sidebar-collapsed", "true");
        const setItem = vi.spyOn(Storage.prototype, "setItem");

        render(<AppShell><div>內容</div></AppShell>);

        await waitFor(() =>
            expect(screen.getByRole("button", { name: "展開側邊欄" })).toBeInTheDocument(),
        );
        expect(setItem).not.toHaveBeenCalledWith("nhi-sidebar-collapsed", "false");
    });

    it("stays usable when local storage is unavailable", async () => {
        vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
            throw new DOMException("blocked", "SecurityError");
        });
        vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
            throw new DOMException("blocked", "SecurityError");
        });

        expect(() => render(<AppShell><div>內容</div></AppShell>)).not.toThrow();
        await waitFor(() =>
            expect(screen.getByRole("button", { name: "收合側邊欄" })).toBeInTheDocument(),
        );
    });

    it("shows a 最近對話 section and no separate 對話 nav item", async () => {
        render(<AppShell><div>內容</div></AppShell>);
        await waitFor(() =>
            expect(screen.getByRole("button", { name: "收合側邊欄" })).toBeInTheDocument(),
        );

        expect(screen.getByText("最近對話")).toBeInTheDocument();
        expect(screen.queryByRole("link", { name: "對話" })).not.toBeInTheDocument();

        // /chat is still reachable through 新對話, which replaces the old entry.
        const newChatLinks = screen.getAllByRole("link", { name: "新對話" });
        expect(newChatLinks.some((link) => link.getAttribute("href") === "/chat")).toBe(true);
    });

    it("links the footer to /settings and keeps 設定 out of the main nav", async () => {
        render(<AppShell><div>內容</div></AppShell>);
        const mainNav = screen.getAllByRole("navigation", { name: "主要導覽" })[0];
        expect(within(mainNav).queryByRole("link", { name: "設定" })).not.toBeInTheDocument();
        expect(screen.queryByText("外觀")).not.toBeInTheDocument();
        const settingsLinks = screen.getAllByRole("link", { name: "設定" });
        expect(settingsLinks.length).toBeGreaterThanOrEqual(2); // sidebar footer + mobile header
        expect(settingsLinks.every((link) => link.getAttribute("href") === "/settings")).toBe(true);
    });
});
