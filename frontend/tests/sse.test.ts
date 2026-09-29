import { afterEach, describe, expect, it, vi } from "vitest";
import { consumeSseStream, parseSseBlock, type SseEventTable } from "../lib/api/sse";

type Ev = { type: "ping"; n: number } | { type: "done" };

const table: SseEventTable<Ev> = {
    ping: (payload) => ({ type: "ping", n: Number(payload.n) }),
    done: () => ({ type: "done" }),
};

describe("parseSseBlock", () => {
    it("dispatches by payload type through the lookup table", () => {
        expect(parseSseBlock('data: {"type":"ping","n":1}', table)).toEqual({ type: "ping", n: 1 });
    });

    it("falls back to the SSE event name when the payload has no type", () => {
        expect(parseSseBlock('event: ping\ndata: {"n":2}', table)).toEqual({ type: "ping", n: 2 });
    });

    it("skips comment/heartbeat lines and blocks without data", () => {
        expect(parseSseBlock(': heartbeat', table)).toBeNull();
        expect(parseSseBlock(': hb\ndata: {"type":"done"}', table)).toEqual({ type: "done" });
    });

    it("ignores the [DONE] sentinel and unknown event types", () => {
        expect(parseSseBlock("data: [DONE]", table)).toBeNull();
        expect(parseSseBlock('data: {"type":"mystery"}', table)).toBeNull();
        expect(parseSseBlock('data: {"type":"constructor"}', table)).toBeNull();
    });

    it("throws chat_stream_invalid for malformed or non-object JSON", () => {
        expect(() => parseSseBlock("data: not-json", table)).toThrowError(
            expect.objectContaining({ status: 502, code: "chat_stream_invalid" }),
        );
        expect(() => parseSseBlock("data: 42", table)).toThrowError(
            expect.objectContaining({ code: "chat_stream_invalid" }),
        );
    });
});

const encoder = new TextEncoder();

function sseResponse(chunks: string[], options: { hang?: AbortSignal } = {}): Response {
    let index = 0;
    const body = new ReadableStream<Uint8Array>({
        pull(controller) {
            if (index < chunks.length) {
                controller.enqueue(encoder.encode(chunks[index++]));
                return;
            }
            const signal = options.hang;
            if (!signal) {
                controller.close();
                return;
            }
            return new Promise<void>((_resolve, reject) => {
                signal.addEventListener("abort", () =>
                    reject(new DOMException("Aborted", "AbortError")),
                );
            });
        },
    });
    return new Response(body, { status: 200 });
}

async function collect(chunks: string[]): Promise<string[]> {
    vi.stubGlobal("fetch", vi.fn(async () => sseResponse(chunks)));
    const blocks: string[] = [];
    let finished = false;
    await consumeSseStream(
        "/x",
        {},
        (block) => {
            blocks.push(block);
            if (block.includes("done")) finished = true;
        },
        () => finished,
    );
    return blocks;
}

describe("consumeSseStream", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.useRealTimers();
    });

    it("reassembles an event split across two chunks", async () => {
        const blocks = await collect(['data: {"type":"pi', 'ng"}\n\ndata: {"type":"done"}\n\n']);
        expect(blocks).toEqual(['data: {"type":"ping"}', 'data: {"type":"done"}']);
    });

    it("emits multiple events from one chunk, including heartbeat comments", async () => {
        const blocks = await collect([': hb\n\ndata: {"a":1}\r\n\r\ndata: {"type":"done"}\n\n']);
        expect(blocks).toEqual([": hb", 'data: {"a":1}', 'data: {"type":"done"}']);
    });

    it("flushes a trailing block without a final blank line", async () => {
        const blocks = await collect(['data: {"type":"done"}']);
        expect(blocks).toEqual(['data: {"type":"done"}']);
    });

    it("fails with chat_stream_interrupted when the stream ends before completion", async () => {
        vi.stubGlobal("fetch", vi.fn(async () => sseResponse(['data: {"a":1}\n\n'])));
        await expect(
            consumeSseStream("/x", {}, () => undefined, () => false),
        ).rejects.toMatchObject({ code: "chat_stream_interrupted" });
    });

    it("rethrows the caller's abort unchanged", async () => {
        const caller = new AbortController();
        vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) =>
            sseResponse(['data: {"a":1}\n\n'], { hang: init?.signal as AbortSignal }),
        ));
        const request = consumeSseStream("/x", {}, () => caller.abort(), () => false, {
            signal: caller.signal,
        });
        await expect(request).rejects.toMatchObject({ name: "AbortError" });
    });

    it("rejects immediately when the caller's signal is already aborted", async () => {
        const caller = new AbortController();
        caller.abort();
        vi.stubGlobal("fetch", vi.fn((_url: string, init?: RequestInit) =>
            init?.signal?.aborted
                ? Promise.reject(new DOMException("Aborted", "AbortError"))
                : Promise.resolve(sseResponse([])),
        ));
        await expect(
            consumeSseStream("/x", {}, () => undefined, () => false, { signal: caller.signal }),
        ).rejects.toMatchObject({ name: "AbortError" });
    });

    it("times out with chat_timeout after 45s of silence mid-stream", async () => {
        vi.useFakeTimers();
        vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) =>
            sseResponse(['data: {"a":1}\n\n'], { hang: init?.signal as AbortSignal }),
        ));
        const request = consumeSseStream("/x", {}, () => undefined, () => false);
        const rejection = expect(request).rejects.toMatchObject({ status: 504, code: "chat_timeout" });
        await vi.advanceTimersByTimeAsync(44_000);
        await vi.advanceTimersByTimeAsync(1_000);
        await rejection;
    });
});
