"use client";

import { useEffect, useState } from "react";

/** Keep live durations moving independently of telemetry polling. */
export function useNow(intervalMs = 1_000): number {
    const [now, setNow] = useState(() => Date.now());

    useEffect(() => {
        const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
        return () => window.clearInterval(timer);
    }, [intervalMs]);

    return now;
}
