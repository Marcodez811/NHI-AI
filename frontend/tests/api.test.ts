import { describe, expect, it, vi } from "vitest";
import { ApiError, getApiErrorMessage } from "../lib/api/client";
import { deleteFolder, fetchDocumentList } from "../lib/api/documents";
import { parseSseEventBlock, streamSlideJobOutlineMessage } from "../lib/api/slides";

describe("API error contracts", () => {
    it("extracts string, object, and FastAPI validation details", () => {
        expect(getApiErrorMessage({ detail: "Folder is not empty." })).toBe(
            "Folder is not empty.",
        );
        expect(getApiErrorMessage({ detail: { message: "Conflict" } })).toBe(
            "Conflict",
        );
        expect(
            getApiErrorMessage({
                detail: [
                    { loc: ["body", "name"], msg: "Field required" },
                    { msg: "Must be unique" },
                ],
            }),
        ).toBe("body.name: Field required; Must be unique");
        expect(getApiErrorMessage("upstream unavailable")).toBe(
            "upstream unavailable",
        );
    });

    it("preserves structured error details through request helpers", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn(async () =>
                new Response(
                    JSON.stringify({
                        detail: { message: "Folder is not empty.", code: "folder_not_empty" },
                    }),
                    { status: 409, headers: { "Content-Type": "application/json" } },
                ),
            ),
        );

        await expect(fetchDocumentList()).rejects.toBeInstanceOf(ApiError);
        try {
            await fetchDocumentList();
        } catch (error) {
            expect(error).toMatchObject({
                status: 409,
                message: "Folder is not empty.",
                code: "folder_not_empty",
            });
        }
    });

    it("accepts a successful 204 response without attempting JSON parsing", async () => {
        const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
        vi.stubGlobal("fetch", fetchMock);

        await expect(deleteFolder("folder-1")).resolves.toBeUndefined();
        expect(fetchMock).toHaveBeenCalledWith(
            "/api/v1/documents/folders/folder-1",
            expect.objectContaining({ method: "DELETE" }),
        );
    });
});

// The planner (slides outline chat) shares the legacy retrieval-chat SSE vocabulary and the
// generic framing engine in lib/api/sse.ts (`consumeSseStream`). These cases exercise that shared
// engine through the one legacy caller still in use; the chat v2 vocabulary has its own tests
// in tests/chatStreamApi.test.ts.
describe("SSE contract (shared legacy framing engine)", () => {
    it("parses data fields and citations from a complete event block", () => {
        expect(
            parseSseEventBlock(
                'event: done\ndata: {"type":"done","citations":[{"type":"file_citation","text":"政策.pdf","filename":"政策.pdf","document_id":"doc-1"}]}',
            ),
        ).toEqual({
            type: "done",
            citations: [
                expect.objectContaining({
                    type: "file_citation",
                    text: "政策.pdf",
                    filename: "政策.pdf",
                    document_id: "doc-1",
                }),
            ],
            mode: undefined,
            grounded: undefined,
        });
    });

    it("handles events split between ReadableStream chunks", async () => {
        const chunks = [
            'event: text_delta\ndata: {"type":"text_delta","text":"早',
            '安"}\n\nevent: done\ndata: {"type":"done","citations":[]}\n\n',
        ];
        const fetchMock = vi.fn(async () =>
            new Response(
                new ReadableStream({
                    start(controller) {
                        for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
                        controller.close();
                    },
                }),
                { headers: { "Content-Type": "text/event-stream" } },
            ),
        );
        vi.stubGlobal("fetch", fetchMock);
        const deltas: string[] = [];
        const done = vi.fn();

        await streamSlideJobOutlineMessage("job-1", "請調整語氣", {
            onDelta: (text) => deltas.push(text),
            onDone: done,
        });

        expect(deltas).toEqual(["早安"]);
        expect(done).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenCalledWith(
            "/api/v1/slides/jobs/job-1/outline/messages",
            expect.any(Object),
        );
    });

    it("reports progress status before structured completion", async () => {
        vi.stubGlobal("fetch", vi.fn(async () => new Response(
            'data: {"type":"status","phase":"searching"}\n\n' +
            'data: {"type":"done","citations":[]}\n\n',
        )));
        const onStatus = vi.fn();

        await streamSlideJobOutlineMessage("job-1", "請調整語氣", { onDelta: vi.fn(), onStatus });

        expect(onStatus).toHaveBeenCalledWith("searching");
    });

    it("rejects EOF without a structured done event", async () => {
        vi.stubGlobal("fetch", vi.fn(async () => new Response(
            'data: {"type":"text_delta","text":"部分回答"}\n\n',
        )));

        await expect(streamSlideJobOutlineMessage("job-1", "請調整語氣", { onDelta: vi.fn() }))
            .rejects.toMatchObject({ code: "chat_stream_interrupted" });
    });

    it("rejects malformed recognized event payloads", async () => {
        expect(() => parseSseEventBlock("event: text_delta\ndata: not-json"))
            .toThrowError(expect.objectContaining({ code: "chat_stream_invalid" }));
    });

    it("does not treat a provider DONE sentinel as application success", async () => {
        vi.stubGlobal("fetch", vi.fn(async () => new Response("data: [DONE]\n\n")));

        await expect(streamSlideJobOutlineMessage("job-1", "請調整語氣", { onDelta: vi.fn() }))
            .rejects.toMatchObject({ code: "chat_stream_interrupted" });
    });

    it("times out when no response bytes arrive", async () => {
        vi.useFakeTimers();
        try {
            vi.stubGlobal("fetch", vi.fn((_url, init) => new Promise<Response>((_resolve, reject) => {
                (init?.signal as AbortSignal).addEventListener("abort", () => {
                    reject(new DOMException("Aborted", "AbortError"));
                });
            })));
            const request = streamSlideJobOutlineMessage("job-1", "請調整語氣", { onDelta: vi.fn() });
            const rejection = expect(request).rejects.toMatchObject({
                code: "chat_timeout",
            });

            await vi.advanceTimersByTimeAsync(45_000);

            await rejection;
        } finally {
            vi.useRealTimers();
        }
    });
});
