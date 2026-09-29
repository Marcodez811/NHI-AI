import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStickToBottom } from "../lib/hooks/useStickToBottom";

type Inputs = { sessionId: string | null; loading: boolean; latestUserKey: string | null };
let resize: (() => void) | null = null;
let motionReduced = false;
let position = 0;
const scrollTo = vi.fn((options: ScrollToOptions) => {
    position = Math.min(options.top ?? 0, 1000);
    window.dispatchEvent(new Event("scroll"));
});

function ChatSurface(props: Inputs) {
    const { contentRef, showLatest, scrollToBottom } = useStickToBottom(props);
    return (
        <section ref={contentRef}>
            <div>對話內容</div>
            {showLatest && <button type="button" onClick={() => scrollToBottom(true)}>最新訊息</button>}
        </section>
    );
}

function moveTo(top: number) {
    act(() => {
        position = top;
        window.dispatchEvent(new Event("scroll"));
    });
}

function growContent() {
    act(() => { resize?.(); });
}

describe("useStickToBottom", () => {
    beforeEach(() => {
        position = 0;
        motionReduced = false;
        resize = null;
        Object.defineProperty(window, "scrollY", { configurable: true, get: () => position });
        Object.defineProperty(window, "innerHeight", { configurable: true, value: 500 });
        Object.defineProperty(document.documentElement, "scrollHeight", { configurable: true, value: 1500 });
        vi.stubGlobal("scrollTo", scrollTo);
        vi.stubGlobal("ResizeObserver", class {
            constructor(callback: () => void) { resize = callback; }
            observe() {}
            disconnect() { resize = null; }
        });
        vi.stubGlobal("matchMedia", () => ({ matches: motionReduced }));
    });
    afterEach(() => {
        cleanup();
        vi.unstubAllGlobals();
        scrollTo.mockClear();
    });

    it("follows content growth near the bottom, stops beyond 80px, and resumes smoothly on click", async () => {
        render(<ChatSurface sessionId="conversation" loading={false} latestUserKey="user-1" />);
        scrollTo.mockClear();
        moveTo(930); // 70 px away, still following.
        growContent();
        expect(scrollTo).toHaveBeenCalledWith({ top: 1500, behavior: "instant" });
        scrollTo.mockClear();
        moveTo(900); // 100 px away, now reading earlier messages.
        expect(screen.getByRole("button", { name: "最新訊息" })).toBeInTheDocument();
        growContent();
        expect(scrollTo).not.toHaveBeenCalled();
        await userEvent.click(screen.getByRole("button", { name: "最新訊息" }));
        expect(scrollTo).toHaveBeenCalledWith({ top: 1500, behavior: "smooth" });
        expect(screen.queryByRole("button", { name: "最新訊息" })).not.toBeInTheDocument();
        scrollTo.mockClear();
        growContent();
        expect(scrollTo).toHaveBeenCalledWith({ top: 1500, behavior: "instant" });
    });

    it("opens existing conversations and sends new turns at the bottom even after scrolling up", () => {
        const view = render(<ChatSurface sessionId="one" loading={true} latestUserKey={null} />);
        expect(scrollTo).not.toHaveBeenCalled();
        view.rerender(<ChatSurface sessionId="one" loading={false} latestUserKey="old-user" />);
        expect(scrollTo).toHaveBeenCalledWith({ top: 1500, behavior: "instant" });
        moveTo(100);
        scrollTo.mockClear();
        view.rerender(<ChatSurface sessionId="one" loading={false} latestUserKey="new-user" />);
        expect(scrollTo).toHaveBeenCalledWith({ top: 1500, behavior: "instant" });
    });

    it("uses instant scrolling when reduced motion is requested", async () => {
        motionReduced = true;
        render(<ChatSurface sessionId="one" loading={false} latestUserKey={null} />);
        moveTo(100);
        scrollTo.mockClear();
        await userEvent.click(screen.getByRole("button", { name: "最新訊息" }));
        expect(scrollTo).toHaveBeenCalledWith({ top: 1500, behavior: "instant" });
    });
});
