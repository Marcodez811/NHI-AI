/** Shared HTTP client: base URL, request wrapper, and error parsing. */

export interface ApiErrorOptions {
    code?: string;
    details?: unknown;
}

export class ApiError extends Error {
    readonly status: number;
    readonly code?: string;
    readonly details?: unknown;

    constructor(status: number, message: string, options: ApiErrorOptions = {}) {
        super(message);
        this.name = "ApiError";
        this.status = status;
        this.code = options.code;
        this.details = options.details;
        Object.setPrototypeOf(this, ApiError.prototype);
    }
}

export const API_ROOT = "/api/v1";
const DEFAULT_ERROR_MESSAGE = "請求失敗，請稍後再試。";

export type UnknownRecord = Record<string, unknown>;

export function isRecord(value: unknown): value is UnknownRecord {
    return typeof value === "object" && value !== null;
}

export function stringValue(value: unknown): string | undefined {
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export function rawStringValue(value: unknown): string | undefined {
    return typeof value === "string" ? value : undefined;
}


function validationMessage(value: unknown): string | undefined {
    if (typeof value === "string") return stringValue(value);
    if (Array.isArray(value)) {
        const messages = value
            .map((item) => {
                if (typeof item === "string") return item.trim();
                if (!isRecord(item)) return undefined;
                const message =
                    stringValue(item.msg) ??
                    stringValue(item.message) ??
                    stringValue(item.detail);
                if (!message) return undefined;
                const location = Array.isArray(item.loc)
                    ? item.loc
                          .filter(
                              (part): part is string | number =>
                                  typeof part === "string" || typeof part === "number",
                          )
                          .join(".")
                    : undefined;
                return location ? `${location}: ${message}` : message;
            })
            .filter((message): message is string => Boolean(message));
        return messages.length ? messages.join("; ") : undefined;
    }
    if (isRecord(value)) {
        return (
            stringValue(value.message) ??
            stringValue(value.msg) ??
            validationMessage(value.detail) ??
            validationMessage(value.error)
        );
    }
    return undefined;
}

/** Extracts FastAPI/Pydantic and provider error payloads into one message. */

export function getApiErrorMessage(
    payload: unknown,
    fallback = DEFAULT_ERROR_MESSAGE,
): string {
    return validationMessage(payload) ?? fallback;
}


export async function readResponseBody(response: Response): Promise<unknown> {
    const text = await response.text();
    if (!text.trim()) return undefined;
    try {
        return JSON.parse(text) as unknown;
    } catch {
        return text;
    }
}


export function errorCode(payload: unknown): string | undefined {
    if (!isRecord(payload)) return undefined;
    return (
        stringValue(payload.code) ??
        (isRecord(payload.detail) ? stringValue(payload.detail.code) : undefined)
    );
}


export async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const headers = new Headers(init?.headers);
    if (!headers.has("Accept")) headers.set("Accept", "application/json");
    let response: Response;
    try {
        response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
    } catch {
        throw new ApiError(0, "網路連線暫時無法使用，請稍後再試。");
    }
    const body = await readResponseBody(response);

    if (!response.ok) {
        throw new ApiError(response.status, getApiErrorMessage(body), {
            code: errorCode(body),
            details: body,
        });
    }
    // A successful DELETE commonly returns 204 and has no JSON body.
    return body as T;
}


export function queryString(values: object): string {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(
        values as Record<string, string | number | boolean | null | undefined>,
    )) {
        if (value !== undefined && value !== null && value !== "") {
            query.set(key, String(value));
        }
    }
    const encoded = query.toString();
    return encoded ? `?${encoded}` : "";
}

