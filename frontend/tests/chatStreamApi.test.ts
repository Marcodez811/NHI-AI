import { describe, expect, it, vi } from "vitest";
import { parseChatEventBlock, sendChatMessage } from "../lib/api/chat";

describe("chat v2 SSE event parser", () => {
    it("parses message_start", () => {
        expect(parseChatEventBlock('data: {"type":"message_start","message_id":"m1"}')).toEqual({
            type: "message_start",
            message_id: "m1",
        });
    });

    it("parses text_delta", () => {
        expect(parseChatEventBlock('data: {"type":"text_delta","text":"你好"}')).toEqual({
            type: "text_delta",
            text: "你好",
        });
    });

    it("parses reasoning and compaction events", () => {
        expect(parseChatEventBlock('data: {"type":"reasoning_delta","text":"先核對資料"}')).toEqual({
            type: "reasoning_delta", text: "先核對資料",
        });
        expect(parseChatEventBlock('data: {"type":"compacting"}')).toEqual({ type: "compacting" });
        expect(parseChatEventBlock('data: {"type":"compacted","ok":true}')).toEqual({ type: "compacted", ok: true });
        expect(parseChatEventBlock('data: {"type":"compacted","ok":false}')).toEqual({ type: "compacted", ok: false });
        expect(() => parseChatEventBlock('data: {"type":"compacted","ok":"false"}')).toThrowError(
            expect.objectContaining({ code: "chat_stream_invalid" }),
        );
    });

    it("parses tool_started and tool_finished", () => {
        expect(
            parseChatEventBlock(
                'data: {"type":"tool_started","tool":"search_knowledge_base","label":"搜尋知識庫：健保藥費"}',
            ),
        ).toEqual({ type: "tool_started", tool: "search_knowledge_base", label: "搜尋知識庫：健保藥費" });
        expect(
            parseChatEventBlock(
                'data: {"type":"tool_finished","tool":"search_knowledge_base","label":"找到 6 筆資料"}',
            ),
        ).toEqual({ type: "tool_finished", tool: "search_knowledge_base", label: "找到 6 筆資料" });
    });

    it("parses sources", () => {
        expect(
            parseChatEventBlock('data: {"type":"sources","sources":[{"name":"政策.pdf","snippet":"摘要"}]}'),
        ).toEqual({ type: "sources", sources: [{ name: "政策.pdf", snippet: "摘要" }] });
    });

    it("parses done", () => {
        expect(
            parseChatEventBlock('data: {"type":"done","message_id":"m1","title":"健保政策問答"}'),
        ).toEqual({ type: "done", message_id: "m1", title: "健保政策問答" });
    });

    it("parses error", () => {
        expect(
            parseChatEventBlock(
                'data: {"type":"error","code":"model_unavailable","message":"模型暫時無法使用"}',
            ),
        ).toEqual({ type: "error", code: "model_unavailable", message: "模型暫時無法使用" });
    });

    it("ignores heartbeats and other unrecognized events", () => {
        expect(parseChatEventBlock(": heartbeat")).toBeNull();
        expect(parseChatEventBlock('data: {"type":"heartbeat"}')).toBeNull();
        expect(parseChatEventBlock('data: {"type":"future_event","payload":42}')).toBeNull();
        expect(parseChatEventBlock("")).toBeNull();
    });

    it("rejects malformed JSON", () => {
        expect(() => parseChatEventBlock("data: not-json")).toThrowError(
            expect.objectContaining({ code: "chat_stream_invalid" }),
        );
    });
});

function sseResponse(chunks: string[]) {
    return new Response(
        new ReadableStream({
            start(controller) {
                for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
                controller.close();
            },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
    );
}

describe("sendChatMessage", () => {
    it("dispatches every event type in order and resolves once done arrives", async () => {
        const events = [
            { type: "message_start", message_id: "m1" },
            { type: "compacting" },
            { type: "compacted", ok: false },
            { type: "reasoning_delta", text: "先核對資料" },
            { type: "future_event" },
            { type: "tool_started", tool: "search_knowledge_base", label: "搜尋知識庫：健保藥費" },
            { type: "text_delta", text: "健保" },
            { type: "text_delta", text: "藥費" },
            { type: "tool_finished", tool: "search_knowledge_base", label: "找到 6 筆資料" },
            { type: "sources", sources: [{ name: "政策.pdf", snippet: "摘要" }] },
            { type: "done", message_id: "m1", title: "健保藥費問答" },
        ];
        const body = events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
        const fetchMock = vi.fn(async () => sseResponse([body]));
        vi.stubGlobal("fetch", fetchMock);

        const calls: string[] = [];
        const handlers = {
            onMessageStart: vi.fn((id: string) => calls.push(`start:${id}`)),
            onCompacting: vi.fn(() => calls.push("compacting")),
            onCompacted: vi.fn((ok: boolean) => calls.push(`compacted:${ok}`)),
            onReasoningDelta: vi.fn((text: string) => calls.push(`reasoning:${text}`)),
            onDelta: vi.fn((text: string) => calls.push(`delta:${text}`)),
            onToolStarted: vi.fn((tool: string) => calls.push(`tool_started:${tool}`)),
            onToolFinished: vi.fn((tool: string) => calls.push(`tool_finished:${tool}`)),
            onSources: vi.fn((sources: { name: string }[]) => calls.push(`sources:${sources.length}`)),
            onDone: vi.fn((id: string, title: string) => calls.push(`done:${id}:${title}`)),
        };

        await sendChatMessage(
            "session-1",
            { content: "問題", attachment_ids: [], model: "gpt-6-astra" },
            handlers,
        );

        expect(calls).toEqual([
            "start:m1",
            "compacting",
            "compacted:false",
            "reasoning:先核對資料",
            "tool_started:search_knowledge_base",
            "delta:健保",
            "delta:藥費",
            "tool_finished:search_knowledge_base",
            "sources:1",
            "done:m1:健保藥費問答",
        ]);
        expect(fetchMock).toHaveBeenCalledWith(
            "/api/v1/chat/sessions/session-1/messages",
            expect.any(Object),
        );
    });

    it("rejects on a structured error event", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn(async () =>
                sseResponse(['data: {"type":"error","code":"model_unavailable","message":"模型暫時無法使用"}\n\n']),
            ),
        );

        await expect(
            sendChatMessage("session-1", { content: "問題", attachment_ids: [], model: "x" }, { onDelta: vi.fn() }),
        ).rejects.toMatchObject({ code: "model_unavailable", message: "模型暫時無法使用" });
    });

    it("rejects EOF without a structured done event", async () => {
        vi.stubGlobal(
            "fetch",
            vi.fn(async () => sseResponse(['data: {"type":"text_delta","text":"部分"}\n\n'])),
        );

        await expect(
            sendChatMessage("session-1", { content: "問題", attachment_ids: [], model: "x" }, { onDelta: vi.fn() }),
        ).rejects.toMatchObject({ code: "chat_stream_interrupted" });
    });
});
