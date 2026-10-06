"use client";

import { useEffect, useState } from "react";
import { DEFAULT_CLIENT_CONFIG, fetchClientConfig, type ClientConfig } from "../api/config";

let cached: ClientConfig | null = null;
let inflight: Promise<ClientConfig> | null = null;

function loadClientConfig(): Promise<ClientConfig> {
    if (cached) return Promise.resolve(cached);
    inflight ??= fetchClientConfig()
        .then((value) => (cached = value))
        .catch(() => {
            inflight = null;
            return DEFAULT_CLIENT_CONFIG;
        });
    return inflight;
}

/** Test helper: forget the cached server config. */
export function resetClientConfigCache() {
    cached = null;
    inflight = null;
}

/** Server limits, cached for the page lifetime; the built-in numbers apply until it resolves. */
export function useClientConfig(): ClientConfig {
    const [config, setConfig] = useState<ClientConfig>(cached ?? DEFAULT_CLIENT_CONFIG);
    useEffect(() => {
        let active = true;
        loadClientConfig().then((value) => {
            if (active) setConfig(value);
        });
        return () => {
            active = false;
        };
    }, []);
    return config;
}
