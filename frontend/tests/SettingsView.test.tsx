import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ fetch: vi.fn(), update: vi.fn(), reset: vi.fn(), models: vi.fn() }));
vi.mock("next-themes", () => ({ useTheme: () => ({ theme: "light", setTheme: vi.fn() }) }));
vi.mock("../lib/api/appSettings", () => ({
    fetchAppSettings: api.fetch,
    updateAppSetting: api.update,
    resetAppSetting: api.reset,
}));
vi.mock("../lib/api/config", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../lib/api/config")>()),
    fetchCatalogModels: api.models,
}));

import { SettingsView } from "../components/settings/SettingsView";

const base = {
    description_zh: "說明",
    constraints: {},
    unit_zh: null,
    default_value: 1,
    restart_required: false,
};

function payload(devEnabled: boolean) {
    const user = {
        id: "chat",
        label_zh: "對話",
        level: "user",
        settings: [
            { ...base, key: "chat.default_model", label_zh: "預設對話模型", type: "model", level: "user", value: "m1", default_value: "m1", source: "config" },
            { ...base, key: "chat.hidden_models", label_zh: "對話中可選的模型", type: "model_list", level: "user", value: [], default_value: [], source: "default" },
        ],
    };
    const dev = {
        id: "uploads",
        label_zh: "上傳限制",
        level: "dev",
        settings: [
            { ...base, key: "uploads.chat.max_attachments", label_zh: "對話附件數量上限", type: "int", level: "dev", value: 3, default_value: 10, source: "database", unit_zh: "個", constraints: { min: 1, max: 50 } },
            { ...base, key: "cleanup.reconcile_interval_seconds", label_zh: "對帳間隔", type: "int", level: "dev", value: 900, default_value: 900, source: "env", restart_required: true },
        ],
    };
    return { dev_enabled: devEnabled, groups: devEnabled ? [user, dev] : [user] };
}

beforeEach(() => {
    api.models.mockResolvedValue([
        { id: "m1", label: "模型一", provider: "openai", available: true },
        { id: "m2", label: "模型二", provider: "openai", available: true },
    ]);
});
afterEach(() => {
    cleanup();
    Object.values(api).forEach((mock) => mock.mockReset());
});

it("hides the developer tab when dev settings are disabled", async () => {
    api.fetch.mockResolvedValue(payload(false));
    render(<SettingsView />);
    expect(await screen.findByText("預設對話模型")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "一般" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "開發者" })).toBeNull();
    expect(screen.getByRole("radio", { name: "淺色" })).toBeInTheDocument();
});

it("shows source badges and the restart hint on the developer tab", async () => {
    api.fetch.mockResolvedValue(payload(true));
    render(<SettingsView />);
    await userEvent.click(await screen.findByRole("tab", { name: "開發者" }));
    const row = screen.getByText("對話附件數量上限").closest("div.flex-col") as HTMLElement;
    expect(within(row).getByText("自訂")).toBeInTheDocument();
    const restart = screen.getByText("對帳間隔").closest("div.flex-col") as HTMLElement;
    expect(within(restart).getByText(".env")).toBeInTheDocument();
    expect(within(restart).getAllByText(/需重新啟動/).length).toBeGreaterThan(0);
});

it("confirms before resetting an override", async () => {
    api.fetch.mockResolvedValue(payload(true));
    api.reset.mockResolvedValue({ ...payload(true).groups[1].settings[0], value: 10, source: "default" });
    render(<SettingsView />);
    await userEvent.click(await screen.findByRole("tab", { name: "開發者" }));
    await userEvent.click(screen.getByRole("button", { name: "重設為預設" }));
    expect(api.reset).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole("button", { name: "重設" }));
    await waitFor(() => expect(api.reset).toHaveBeenCalledWith("uploads.chat.max_attachments"));
});

it("saves a number on blur and shows the 422 message inline", async () => {
    api.fetch.mockResolvedValue(payload(true));
    api.update.mockRejectedValue(new Error("「對話附件數量上限」必須介於 1 到 50 之間。"));
    render(<SettingsView />);
    await userEvent.click(await screen.findByRole("tab", { name: "開發者" }));
    const input = screen.getByRole("spinbutton", { name: "對話附件數量上限" });
    await userEvent.clear(input);
    await userEvent.type(input, "99");
    await userEvent.tab();
    await waitFor(() => expect(api.update).toHaveBeenCalledWith("uploads.chat.max_attachments", 99));
    expect(await screen.findByRole("alert")).toHaveTextContent("必須介於 1 到 50");
});

it("hides a model through the checklist", async () => {
    api.fetch.mockResolvedValue(payload(false));
    api.update.mockResolvedValue({ ...payload(false).groups[0].settings[1], value: ["m2"], source: "database" });
    render(<SettingsView />);
    await userEvent.click(await screen.findByRole("switch", { name: "模型二" }));
    await waitFor(() => expect(api.update).toHaveBeenCalledWith("chat.hidden_models", ["m2"]));
});
