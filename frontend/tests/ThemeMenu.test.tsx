import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const themeState = vi.hoisted(() => ({
    theme: "light" as string,
    setTheme: vi.fn(),
}));

vi.mock("next-themes", () => ({
    useTheme: () => themeState,
}));

import { ThemeMenu } from "../components/app-shell/theme-menu";

describe("ThemeMenu", () => {
    afterEach(() => {
        cleanup();
        themeState.theme = "light";
        themeState.setTheme.mockClear();
    });

    it("opens without throwing and selects a theme through the menu radio group", async () => {
        const user = userEvent.setup();
        render(<ThemeMenu />);

        const trigger = screen.getByRole("button", { name: "切換佈景" });
        fireEvent.mouseDown(trigger, { button: 0 });
        await waitFor(() => expect(screen.getByText("顯示模式")).toBeInTheDocument());

        expect(screen.getByRole("menuitemradio", { name: "淺色" })).toHaveAttribute(
            "aria-checked",
            "true",
        );

        await user.click(screen.getByRole("menuitemradio", { name: "深色" }));
        expect(themeState.setTheme).toHaveBeenCalledWith("dark");
    });
});
