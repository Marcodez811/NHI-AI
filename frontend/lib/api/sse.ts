/** Shared SSE plumbing: framing/JSON parsing of one event block and the stream reader. */
import {
    API_ROOT,
    ApiError,
    errorCode,
    getApiErrorMessage,
    isRecord,
    readResponseBody,
    stringValue,
    type UnknownRecord,
} from "./client";

export function invalidStreamError(): ApiError {
    return new ApiError(502, "對話串流格式無效。", { code: "chat_stream_invalid" });
}

/** Maps an event type to a validator/coercer; `null` means "ignore this event". */
export type SseEventTable<E> = Record<string, (payload: UnknownRecord) => E | null>;

/**
 * Parses one complete SSE event block. Framing, comment skipping, the `[DONE]`
 * sentinel, and JSON parsing are shared; each vocabulary supplies a lookup
 * table keyed by event type. Unknown event types are ignored (`null`).
 * Exported so stream behavior can be tested without a ReadableStream.
 */
export function parseSseBlock<E>(block: string, table: SseEventTable<E>): E | null {
    let eventName = "message";
    const dataLines: string[] = [];
    for (const line of block.split(/\r?\n/)) {
        if (!line || line.startsWith(":")) continue;
        const separator = line.indexOf(":");
        const field = separator >= 0 ? line.slice(0, separator) : line;
        const value = separator >= 0 ? line.slice(separator + 1).replace(/^ /, "") : "";
        if (field === "event") eventName = value.trim();
        if (field === "data") dataLines.push(value);
    }
    const rawData = dataLines.join("\n").trim();
    if (!rawData) return null;
    // A provider-style sentinel is not our application completion contract.
    // Only a structured `done` event can make a request successful.
    if (rawData === "[DONE]") return null;

    let payload: unknown;
    try {
        payload = JSON.parse(rawData) as unknown;
    } catch {
        throw invalidStreamError();
    }
    if (!isRecord(payload)) throw invalidStreamError();

    const type = stringValue(payload.type) ?? eventName;
    if (!Object.prototype.hasOwnProperty.call(table, type)) return null;
    return table[type](payload);
}

/**
 * Reads an SSE POST response and forwards each complete event block to
 * `onBlock`, handling chunk buffering, the 45s inactivity timeout, and abort
 * wiring. Event parsing and per-type dispatch belong to the caller, which
 * reports completion through `isFinished`. Shared by the legacy
 * retrieval/slides vocabulary (`streamSse`) and the chat v2 vocabulary
 * (`sendChatMessage`).
 */
export async function consumeSseStream(
    path: string,
    payload: unknown,
    onBlock: (block: string) => void,
    isFinished: () => boolean,
    options: { signal?: AbortSignal } = {},
): Promise<void> {
    const inactivityTimeoutMs = 45_000;
    const controller = new AbortController();
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    let inactivityTimer: ReturnType<typeof setTimeout> | undefined;
    let timedOut = false;
    const abortFromCaller = () => controller.abort();
    const resetInactivityTimer = () => {
        if (inactivityTimer) clearTimeout(inactivityTimer);
        inactivityTimer = setTimeout(() => {
            timedOut = true;
            controller.abort();
        }, inactivityTimeoutMs);
    };

    if (options.signal?.aborted) abortFromCaller();
    else options.signal?.addEventListener("abort", abortFromCaller, { once: true });
    resetInactivityTimer();

    let response: Response;
    try {
        response = await fetch(`${API_ROOT}${path}`, {
            method: "POST",
            headers: {
                Accept: "text/event-stream",
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
            signal: controller.signal,
        });
    } catch (error) {
        if (inactivityTimer) clearTimeout(inactivityTimer);
        options.signal?.removeEventListener("abort", abortFromCaller);
        if (options.signal?.aborted) throw error;
        if (timedOut) {
            throw new ApiError(504, "對話服務回應逾時，請稍後再試。", {
                code: "chat_timeout",
            });
        }
        throw new ApiError(0, "對話服務暫時無法使用。");
    }
    try {
        if (!response.ok) {
            const body = await readResponseBody(response);
            throw new ApiError(
                response.status,
                getApiErrorMessage(body, "對話服務暫時無法使用。"),
                { code: errorCode(body), details: body },
            );
        }
        if (!response.body) throw new ApiError(502, "對話串流沒有回傳內容。");

        reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (!isFinished()) {
            const { value, done } = await reader.read();
            if (value?.byteLength) resetInactivityTimer();
            buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
            let boundary: RegExpExecArray | null;
            while ((boundary = /\r?\n\r?\n/.exec(buffer)) !== null) {
                onBlock(buffer.slice(0, boundary.index));
                buffer = buffer.slice(boundary.index + boundary[0].length);
                if (isFinished()) break;
            }
            if (done) break;
        }
        buffer += decoder.decode();
        if (!isFinished() && buffer.trim()) onBlock(buffer);
        if (!isFinished()) {
            throw new ApiError(502, "對話串流意外中斷，請重新送出問題。", {
                code: "chat_stream_interrupted",
            });
        }
    } catch (error) {
        if (error instanceof ApiError) throw error;
        if (options.signal?.aborted) throw error;
        if (timedOut) {
            throw new ApiError(504, "對話服務回應逾時，請稍後再試。", {
                code: "chat_timeout",
            });
        }
        throw new ApiError(0, "對話服務暫時無法使用。");
    } finally {
        if (inactivityTimer) clearTimeout(inactivityTimer);
        options.signal?.removeEventListener("abort", abortFromCaller);
        if (reader) await reader.cancel().catch(() => undefined);
    }
}

