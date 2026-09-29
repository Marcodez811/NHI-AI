import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatEmptyState } from "../components/chat/ChatEmptyState";

describe("ChatEmptyState", () => {
    afterEach(() => cleanup());

    it("keeps the greeting and renders the composer slot", () => {
        render(
            <ChatEmptyState onPick={vi.fn()}>
                <div data-testid="composer-slot" />
            </ChatEmptyState>,
        );
        expect(screen.getByText("健保署 AI 助理")).toBeInTheDocument();
        expect(screen.getByText(/提出問題或上傳文件/)).toBeInTheDocument();
        expect(screen.getByTestId("composer-slot")).toBeInTheDocument();
    });

    it("renders suggestions as pill buttons that fill the full prompt", async () => {
        const onPick = vi.fn();
        render(<ChatEmptyState onPick={onPick}>{null}</ChatEmptyState>);

        const pill = screen.getByRole("button", { name: "最新健保給付政策重點" });
        await userEvent.click(pill);
        expect(onPick).toHaveBeenCalledWith("幫我彙整最新健保給付政策的重點");

        expect(screen.getByRole("button", { name: "說明上傳文件的關鍵條款" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "藥價調整相關討論" })).toBeInTheDocument();
    });
});
