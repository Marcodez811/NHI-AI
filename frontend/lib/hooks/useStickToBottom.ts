"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

const SCROLL_AWAY_PX = 80;

function distanceFromBottom(): number {
    const page = document.documentElement;
    return Math.max(0, page.scrollHeight - window.innerHeight - window.scrollY);
}

function reducedMotion(): boolean {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

/** The app shell scrolls the window, not a nested chat pane. */
export function useStickToBottom({ sessionId, loading, latestUserKey }: {
    sessionId: string | null;
    loading: boolean;
    latestUserKey: string | null;
}) {
    const contentRef = useRef<HTMLElement>(null);
    const following = useRef(true);
    const resuming = useRef(false);
    const [showLatest, setShowLatest] = useState(false);

    const scrollToBottom = useCallback((smooth = false) => {
        following.current = true;
        setShowLatest(false);
        // Ignore intermediate scroll events from the smooth animation.
        resuming.current = smooth && !reducedMotion();
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: resuming.current ? "smooth" : "instant" });
    }, []);

    useEffect(() => {
        const onScroll = () => {
            if (resuming.current) {
                if (distanceFromBottom() <= SCROLL_AWAY_PX) resuming.current = false;
                return;
            }
            const nearBottom = distanceFromBottom() <= SCROLL_AWAY_PX;
            following.current = nearBottom;
            setShowLatest(!nearBottom);
        };
        // A deliberate gesture interrupts even an in-progress smooth resume.
        const interrupt = () => { resuming.current = false; };
        window.addEventListener("scroll", onScroll, { passive: true });
        window.addEventListener("wheel", interrupt, { passive: true });
        window.addEventListener("touchstart", interrupt, { passive: true });
        return () => {
            window.removeEventListener("scroll", onScroll);
            window.removeEventListener("wheel", interrupt);
            window.removeEventListener("touchstart", interrupt);
        };
    }, []);

    useLayoutEffect(() => {
        if (!loading) scrollToBottom();
    }, [sessionId, loading, scrollToBottom]);

    useLayoutEffect(() => {
        if (latestUserKey) scrollToBottom();
    }, [latestUserKey, scrollToBottom]);

    useEffect(() => {
        const node = contentRef.current;
        if (!node || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(() => {
            if (following.current && !resuming.current) scrollToBottom();
        });
        observer.observe(node);
        return () => observer.disconnect();
    }, [scrollToBottom]);

    return { contentRef, showLatest, scrollToBottom };
}
