import { StrictMode, useState } from "react";
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceRoute } from "../components/workspace/WorkspaceRoute";

const navigation = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
const resetChat = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
    usePathname: () => "/chat",
    useRouter: () => navigation,
}));
vi.mock("../components/workspace/WorkspaceContent", () => ({ WorkspaceContent: () => null }));
vi.mock("../components/workspace/WorkspaceProvider", () => ({
    useWorkspace: () => {
        const [chat, setChat] = useState<unknown[]>([]);
        return {
            chat,
            view: "chat",
            setView: vi.fn(),
            startNewChat: () => {
                resetChat();
                // Bound the broken case so the regression fails without hanging.
                if (resetChat.mock.calls.length > 8) throw new Error("New-chat render loop");
                setChat([]);
            },
        };
    },
}));

afterEach(() => {
    cleanup();
    window.history.replaceState({}, "", "/chat");
});

it("consumes a new-chat request once while router replacement is pending, including Strict Mode", () => {
    window.history.replaceState({}, "", "/chat?new=1");
    const result = render(<StrictMode><WorkspaceRoute /></StrictMode>);
    expect(resetChat).toHaveBeenCalledTimes(1);
    expect(navigation.replace).toHaveBeenCalledTimes(1);

    window.history.replaceState({}, "", "/chat");
    result.rerender(<StrictMode><WorkspaceRoute /></StrictMode>);
    window.history.replaceState({}, "", "/chat?new=1");
    result.rerender(<StrictMode><WorkspaceRoute /></StrictMode>);
    expect(resetChat).toHaveBeenCalledTimes(2);
});
