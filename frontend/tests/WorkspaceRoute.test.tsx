import { StrictMode, useState } from "react";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { resolveWorkspaceView, WorkspaceRoute } from "../components/workspace/WorkspaceRoute";

const navigation = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }));
const resetChat = vi.hoisted(() => vi.fn());
const routePath = vi.hoisted(() => ({ current: "/chat" }));
const renderedView = vi.hoisted(() => vi.fn());
const renderedContent = vi.hoisted(() => vi.fn());
const startSlides = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
    usePathname: () => routePath.current,
    useRouter: () => navigation,
}));
vi.mock("../components/workspace/WorkspaceContent", () => ({
    WorkspaceContent: (props: { view: string; slideJobId?: string | null; onSlideStart?: () => Promise<void> }) => {
        renderedContent(props);
        const { view } = props;
        renderedView(view);
        return null;
    },
}));
vi.mock("../components/workspace/WorkspaceProvider", () => ({
    useWorkspace: () => {
        const [chat, setChat] = useState<unknown[]>([]);
        return {
            chat,
            view: "chat",
            setView: vi.fn(),
            startSlides,
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
    routePath.current = "/chat";
    vi.clearAllMocks();
});

it("resolves the slides index to the slides view when a tab query string is present", () => {
    // usePathname() never carries the query string, so ?tab=recent must not perturb the route-to-view mapping.
    window.history.replaceState({}, "", "/slides?tab=recent");
    routePath.current = "/slides";
    render(<WorkspaceRoute />);
    expect(renderedView).toHaveBeenLastCalledWith("slides");
    expect(renderedContent.mock.lastCall?.[0].slideJobId).toBeNull();
});

it("resolves a job URL to the slides view without accepting unrelated nested routes", () => {
    expect(resolveWorkspaceView("/slides/job-123")).toBe("slides");
    expect(resolveWorkspaceView("/slides/job-123/")).toBe("slides");
    expect(resolveWorkspaceView("/slides/job-123/extra")).toBe("chat");
    routePath.current = "/slides/job-123";
    render(<WorkspaceRoute />);
    expect(renderedView).toHaveBeenLastCalledWith("slides");
    expect(renderedContent.mock.lastCall?.[0].slideJobId).toBe("job-123");
});

it("navigates to the new job URL only after a successful create response", async () => {
    routePath.current = "/slides";
    startSlides.mockResolvedValueOnce({ job_id: "job-new" }).mockResolvedValueOnce(null);
    render(<WorkspaceRoute />);

    const onSlideStart = renderedContent.mock.lastCall?.[0].onSlideStart;
    await act(async () => { await onSlideStart(); });
    expect(navigation.push).toHaveBeenCalledWith("/slides/job-new");

    await act(async () => { await onSlideStart(); });
    expect(navigation.push).toHaveBeenCalledTimes(1);
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
