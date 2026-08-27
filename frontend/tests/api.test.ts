import { describe, expect, it, vi } from "vitest";
import {
    ApiError,
    deleteFolder,
    fetchDocumentList,
    getApiErrorMessage,
    parseSseEventBlock,
    streamChat,
} from "../lib/api";

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

describe("SSE contract", () => {
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

        await streamChat(
            { question: "問題", mode: "legislative_qa", document_ids: [] },
            { onDelta: (text) => deltas.push(text), onDone: done },
        );

        expect(deltas).toEqual(["早安"]);
        expect(done).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenCalledWith(
            "/api/v1/chat/stream",
            expect.any(Object),
        );
    });
});
