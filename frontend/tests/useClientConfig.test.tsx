import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { DEFAULT_CLIENT_CONFIG } from "../lib/api/config";
import { resetClientConfigCache, useClientConfig } from "../lib/hooks/useClientConfig";

const api = vi.hoisted(() => ({ fetchClientConfig: vi.fn() }));
vi.mock("../lib/api/config", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../lib/api/config")>()),
    fetchClientConfig: api.fetchClientConfig,
}));

beforeEach(() => {
    resetClientConfigCache();
    api.fetchClientConfig.mockReset();
});

it("starts from the built-in limits, then uses the server values and caches them", async () => {
    const server = { ...DEFAULT_CLIENT_CONFIG, chat: { ...DEFAULT_CLIENT_CONFIG.chat, max_attachments: 3 } };
    api.fetchClientConfig.mockResolvedValue(server);
    const first = renderHook(() => useClientConfig());
    expect(first.result.current.chat.max_attachments).toBe(10);
    await waitFor(() => expect(first.result.current.chat.max_attachments).toBe(3));
    const second = renderHook(() => useClientConfig());
    expect(second.result.current.chat.max_attachments).toBe(3);
    expect(api.fetchClientConfig).toHaveBeenCalledTimes(1);
});

it("keeps the fallback limits when the request fails", async () => {
    api.fetchClientConfig.mockRejectedValue(new Error("down"));
    const { result } = renderHook(() => useClientConfig());
    await waitFor(() => expect(api.fetchClientConfig).toHaveBeenCalled());
    expect(result.current.chat.max_pdf_bytes).toBe(DEFAULT_CLIENT_CONFIG.chat.max_pdf_bytes);
});
