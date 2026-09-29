/** Chat (agentic) sessions, attachments, models, and the chat SSE stream. */
import {
    API_ROOT,
    ApiError,
    getApiErrorMessage,
    isRecord,
    queryString,
    rawStringValue,
    request,
    stringValue,
} from "./client";
import { consumeSseStream, invalidStreamError, parseSseBlock, type SseEventTable } from "./sse";

export type ChatAttachmentKind = "document" | "image";
export type ChatAttachmentStatus = "ready" | "failed";
export type ChatMessageStatus = "complete" | "interrupted" | "error";

export interface ChatAttachment {
    id: string;
    display_name: string;
    mime_type: string;
    kind: ChatAttachmentKind;
    size_bytes: number;
    status: ChatAttachmentStatus;
    error: string | null;
    text_chars: number | null;
    created_at: string;
}

export interface ChatSourceRef {
    name: string;
    snippet: string;
}

export interface ChatMessageRecord {
    id: string;
    role: "user" | "assistant";
    content: string;
    reasoning?: string | null;
    attachment_ids?: string[] | null;
    sources?: ChatSourceRef[] | null;
    status: ChatMessageStatus;
    model?: string | null;
    created_at: string;
}

export interface ChatSessionSummary {
    id: string;
    title: string;
    updated_at: string;
}

export interface ChatSessionDetail extends ChatSessionSummary {
    model: string;
    created_at: string;
    compacted_through_message_id: string | null;
    messages: ChatMessageRecord[];
    attachments: ChatAttachment[];
}

export interface ChatModelOption {
    id: string;
    label: string;
    provider: string;
    available: boolean;
}

export interface ChatModelListResponse {
    models: ChatModelOption[];
    default: string;
}

export interface ChatSendMessagePayload {
    content: string;
    attachment_ids: string[];
    model: string;
}

/** Kinds accepted by the chat attachment picker; enforced again server-side. */
const CHAT_DOCUMENT_EXTENSIONS = [".pdf", ".docx", ".txt", ".md"] as const;
const CHAT_IMAGE_MIME_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;
export const CHAT_MAX_DOCUMENT_BYTES = 25 * 1024 * 1024;
/** PDFs go to the model as files, so they must fit the providers' inline limits. */
export const CHAT_MAX_PDF_BYTES = 20 * 1024 * 1024;
export const CHAT_MAX_IMAGE_BYTES = 10 * 1024 * 1024;
export const CHAT_MAX_ATTACHMENTS = 10;

/** Classifies a picked file the way the server will, for instant client-side feedback. */
export function classifyChatAttachment(file: File): ChatAttachmentKind | null {
    if ((CHAT_IMAGE_MIME_TYPES as readonly string[]).includes(file.type)) return "image";
    const name = file.name.toLowerCase();
    if (CHAT_DOCUMENT_EXTENSIONS.some((extension) => name.endsWith(extension))) return "document";
    return null;
}

export async function fetchChatModels(
    options: { signal?: AbortSignal } = {},
): Promise<ChatModelListResponse> {
    return request<ChatModelListResponse>("/chat/models", options);
}

export async function fetchChatSessions(
    options: { signal?: AbortSignal } = {},
): Promise<ChatSessionSummary[]> {
    return request<ChatSessionSummary[]>("/chat/sessions", options);
}

export async function createChatSession(model?: string): Promise<ChatSessionDetail> {
    return request<ChatSessionDetail>("/chat/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(model ? { model } : {}),
    });
}

export async function fetchChatSession(
    id: string,
    options: { signal?: AbortSignal } = {},
): Promise<ChatSessionDetail> {
    return request<ChatSessionDetail>(`/chat/sessions/${encodeURIComponent(id)}`, options);
}

export async function renameChatSession(id: string, title: string): Promise<ChatSessionSummary> {
    return request<ChatSessionSummary>(`/chat/sessions/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
    });
}

export async function deleteChatSession(id: string): Promise<void> {
    await request<void>(`/chat/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function uploadChatAttachment(
    sessionId: string,
    file: File,
    options: { signal?: AbortSignal } = {},
): Promise<ChatAttachment> {
    const form = new FormData();
    form.set("file", file);
    return request<ChatAttachment>(
        `/chat/sessions/${encodeURIComponent(sessionId)}/attachments`,
        { method: "POST", body: form, signal: options.signal },
    );
}

/** Source for image thumbnails and opening a document; never shown to users. */
export function chatAttachmentContentUrl(sessionId: string, attachmentId: string): string {
    return `${API_ROOT}/chat/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(attachmentId)}/content`;
}

export async function deleteChatAttachment(sessionId: string, attachmentId: string): Promise<void> {
    await request<void>(
        `/chat/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(attachmentId)}`,
        { method: "DELETE" },
    );
}

/** The chat SSE vocabulary; see `docs/9_29_chat_context_and_ux_spec.md`. */
export type ChatEvent =
    | { type: "message_start"; message_id: string }
    | { type: "reasoning_delta"; text: string }
    | { type: "compacting" }
    | { type: "compacted"; ok: boolean }
    | { type: "text_delta"; text: string }
    | { type: "tool_started"; tool: string; label: string }
    | { type: "tool_finished"; tool: string; label: string }
    | { type: "sources"; sources: ChatSourceRef[] }
    | { type: "done"; message_id: string; title: string }
    | { type: "error"; message: string; code?: string };


function chatSourceRef(value: unknown): ChatSourceRef | null {
    if (!isRecord(value)) return null;
    const name = stringValue(value.name);
    if (!name) return null;
    return { name, snippet: rawStringValue(value.snippet) ?? "" };
}

const toolEvent =
    (type: "tool_started" | "tool_finished"): SseEventTable<ChatEvent>[string] =>
    (payload) => {
        const tool = stringValue(payload.tool);
        if (!tool) throw invalidStreamError();
        return { type, tool, label: stringValue(payload.label) ?? "" };
    };

const textEvent =
    (type: "text_delta" | "reasoning_delta"): SseEventTable<ChatEvent>[string] =>
    (payload) => {
        const text = rawStringValue(payload.text);
        if (text === undefined) throw invalidStreamError();
        return { type, text };
    };

const CHAT_EVENTS: SseEventTable<ChatEvent> = {
    message_start: (payload) => {
        const messageId = stringValue(payload.message_id);
        if (!messageId) throw invalidStreamError();
        return { type: "message_start", message_id: messageId };
    },
    text_delta: textEvent("text_delta"),
    reasoning_delta: textEvent("reasoning_delta"),
    compacting: () => ({ type: "compacting" }),
    compacted: (payload) => {
        if (typeof payload.ok !== "boolean") throw invalidStreamError();
        return { type: "compacted", ok: payload.ok };
    },
    tool_started: toolEvent("tool_started"),
    tool_finished: toolEvent("tool_finished"),
    sources: (payload) => ({
        type: "sources",
        sources: Array.isArray(payload.sources)
            ? payload.sources
                  .map(chatSourceRef)
                  .filter((item): item is ChatSourceRef => item !== null)
            : [],
    }),
    done: (payload) => ({
        type: "done",
        message_id: stringValue(payload.message_id) ?? "",
        title: stringValue(payload.title) ?? "",
    }),
    error: (payload) => ({
        type: "error",
        message: stringValue(payload.message) ?? getApiErrorMessage(payload, "對話服務發生錯誤。"),
        code: stringValue(payload.code),
    }),
    // Unrecognized events (e.g. heartbeats surfaced as data) are ignored.
};

/**
 * Parses one complete SSE event in the chat v2 vocabulary. Exported so
 * stream behavior can be tested without constructing a ReadableStream.
 */
export function parseChatEventBlock(block: string): ChatEvent | null {
    return parseSseBlock(block, CHAT_EVENTS);
}

export interface ChatStreamHandlers {
    onDelta: (text: string) => void;
    onReasoningDelta?: (text: string) => void;
    onCompacting?: () => void;
    onCompacted?: (ok: boolean) => void;
    onMessageStart?: (messageId: string) => void;
    onToolStarted?: (tool: string, label: string) => void;
    onToolFinished?: (tool: string, label: string) => void;
    onSources?: (sources: ChatSourceRef[]) => void;
    onDone?: (messageId: string, title: string) => void;
}

/** Sends one chat turn and streams the assistant's reply through the chat v2 SSE vocabulary. */
export async function sendChatMessage(
    sessionId: string,
    payload: ChatSendMessagePayload,
    handlers: ChatStreamHandlers,
    options: { signal?: AbortSignal } = {},
): Promise<void> {
    let finished = false;
    await consumeSseStream(
        `/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
        payload,
        (block) => {
            const event = parseChatEventBlock(block);
            if (!event) return;
            if (event.type === "message_start") handlers.onMessageStart?.(event.message_id);
            else if (event.type === "text_delta") handlers.onDelta(event.text);
            else if (event.type === "reasoning_delta") handlers.onReasoningDelta?.(event.text);
            else if (event.type === "compacting") handlers.onCompacting?.();
            else if (event.type === "compacted") handlers.onCompacted?.(event.ok);
            else if (event.type === "tool_started") handlers.onToolStarted?.(event.tool, event.label);
            else if (event.type === "tool_finished") handlers.onToolFinished?.(event.tool, event.label);
            else if (event.type === "sources") handlers.onSources?.(event.sources);
            else if (event.type === "done") {
                finished = true;
                handlers.onDone?.(event.message_id, event.title);
            } else {
                throw new ApiError(503, event.message, { code: event.code });
            }
        },
        () => finished,
        options,
    );
}

