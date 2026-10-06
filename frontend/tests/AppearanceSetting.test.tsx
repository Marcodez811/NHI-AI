import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

const themeState = vi.hoisted(() => ({ theme: "light" as string, setTheme: vi.fn() }));
vi.mock("next-themes", () => ({ useTheme: () => themeState }));

import { AppearanceSetting } from "../components/settings/AppearanceSetting";

afterEach(() => {
    cleanup();
    themeState.setTheme.mockClear();
});

it("shows the current theme and switches it through next-themes", async () => {
    render(<AppearanceSetting />);
    expect(await screen.findByRole("radio", { name: "淺色" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: "深色" }));
    expect(themeState.setTheme).toHaveBeenCalledWith("dark");
    await userEvent.click(screen.getByRole("radio", { name: "跟隨系統" }));
    expect(themeState.setTheme).toHaveBeenCalledWith("system");
});
