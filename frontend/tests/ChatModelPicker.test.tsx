import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatModelPicker } from "../components/chat/ChatModelPicker";
import type { ChatModelOption } from "../lib/api/chat";

const models: ChatModelOption[] = [
    { id: "gpt-6-astra", label: "GPT-6 Astra", provider: "openai", available: true },
    { id: "litellm/anthropic/claude-sonnet-5", label: "Claude Sonnet 5", provider: "anthropic", available: true },
    { id: "litellm/gemini/gemini-3.8-flash", label: "Gemini 3.8 Flash", provider: "gemini", available: false },
];

describe("ChatModelPicker", () => {
    afterEach(() => cleanup());

    it("marks an unavailable model disabled with 未設定金鑰 and blocks selecting it", async () => {
        const onValueChange = vi.fn();
        render(<ChatModelPicker models={models} value="gpt-6-astra" onValueChange={onValueChange} />);

        const trigger = screen.getByRole("combobox", { name: "選擇模型" });
        fireEvent.mouseDown(trigger, { button: 0 });

        const unavailable = await screen.findByRole("option", { name: /Gemini 3.8 Flash/ });
        expect(unavailable).toHaveTextContent("未設定金鑰");
        expect(unavailable).toHaveAttribute("aria-disabled", "true");

        await userEvent.click(unavailable);
        expect(onValueChange).not.toHaveBeenCalled();
    });

    it("selects an available model", async () => {
        const onValueChange = vi.fn();
        render(<ChatModelPicker models={models} value="gpt-6-astra" onValueChange={onValueChange} />);

        const trigger = screen.getByRole("combobox", { name: "選擇模型" });
        fireEvent.mouseDown(trigger, { button: 0 });

        const available = await screen.findByRole("option", { name: "Claude Sonnet 5" });
        expect(available).not.toHaveAttribute("aria-disabled", "true");
        await userEvent.click(available);
        expect(onValueChange).toHaveBeenCalledWith("litellm/anthropic/claude-sonnet-5");
    });

    it("disables the whole trigger while the model list is loading", () => {
        render(<ChatModelPicker models={[]} value={undefined} onValueChange={vi.fn()} disabled />);
        expect(screen.getByRole("combobox", { name: "選擇模型" })).toBeDisabled();
    });
});
