import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/chat" }));
vi.mock("../components/app-shell/theme-menu", () => ({
    ThemeMenu: () => <button type="button">佈景</button>,
}));

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
});
