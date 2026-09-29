import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkflowModelSettings } from "../components/workspace/WorkflowModelSettings";

const api = vi.hoisted(() => ({
    fetch: vi.fn(),
    update: vi.fn(),
}));

vi.mock("../lib/api/settings", async (importOriginal) => {
    const actual = await importOriginal<typeof import("../lib/api/settings")>();
    return {
        ...actual,
        fetchAgentSettings: api.fetch,
        updateAgentStageSettings: api.update,
    };
});

function stageSettings(stages: string[]) {
    return {
        stages: Object.fromEntries(stages.map((stage) => [
            stage,
            {
                stored: { runner: null, model: null, reasoning_effort: null, planner_enabled: null },
                effective: {
                    runner: { value: "codex", source: "env" },
                    model: { value: "gpt-5-codex", source: "env" },
                    reasoning_effort: { value: "high", source: "default" },
                    planner_enabled: { value: false, source: "env" },
                },
                allowed_runners: stage === "planner" ? ["codex", "agents"] : ["codex"],
            },
        ])),
        providers: { openai: true, gemini: false, anthropic: false },
    };
}

const slidesSettings = stageSettings(["extraction", "planner", "author", "reviewer"]);
const newsSettings = stageSettings(["extraction", "author"]);

afterEach(() => {
    cleanup();
    vi.clearAllMocks();
});

it("renders effective values and their configured sources", async () => {
    api.fetch.mockResolvedValue(slidesSettings);
    render(<WorkflowModelSettings workflow="slides" />);

    const extractionStage = await screen.findByRole("region", { name: "資料擷取" });
    expect(within(extractionStage).getByText("gpt-5-codex")).toBeTruthy();
    expect(screen.getAllByText("來自 .env")).toHaveLength(9);
    expect(screen.getAllByText("來自系統預設")).toHaveLength(4);
    expect(api.fetch).toHaveBeenCalledWith("slides", expect.anything());
});

it("offers only runners allowed for each stage", async () => {
    api.fetch.mockResolvedValue(slidesSettings);
    render(<WorkflowModelSettings workflow="slides" />);

    const runner = await screen.findByLabelText("資料擷取執行器");
    const offered = within(runner).getAllByRole("option")
        .map((option) => (option as HTMLOptionElement).value)
        .filter(Boolean);
    expect(offered).toEqual(["codex"]);
    const plannerRunner = screen.getByLabelText("工作規劃執行器");
    expect(within(plannerRunner).getAllByRole("option").map(
        (option) => (option as HTMLOptionElement).value,
    ).filter(Boolean)).toEqual(["codex", "agents"]);
});

it("clears a stored row when restoring defaults", async () => {
    api.fetch.mockResolvedValue(slidesSettings);
    api.update.mockResolvedValue(slidesSettings);
    render(<WorkflowModelSettings workflow="slides" />);
    const extraction = await screen.findByRole("region", { name: "資料擷取" });
    fireEvent.click(within(extraction).getByRole("button", { name: "恢復預設值" }));
    await screen.findByText("已儲存設定");
    expect(api.update).toHaveBeenCalledWith("slides", "extraction", {
        runner: null, model: null, reasoning_effort: null, planner_enabled: null,
    });
    expect(within(extraction).getByText(/變更只套用於之後建立的工作。/)).toBeTruthy();
});

it("renders only the given workflow's stages", async () => {
    api.fetch.mockResolvedValue(newsSettings);
    render(<WorkflowModelSettings workflow="news" />);

    await screen.findByRole("region", { name: "資料擷取" });
    expect(screen.getByRole("region", { name: "內容撰寫" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "工作規劃" })).toBeNull();
    expect(screen.queryByRole("region", { name: "內容審查" })).toBeNull();
    expect(api.fetch).toHaveBeenCalledWith("news", expect.anything());
});
