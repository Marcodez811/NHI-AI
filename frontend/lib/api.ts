/**
 * Typed client for the public `/api/v1` contracts.
 *
 * The API layer deliberately does not normalize alternative field names. The
 * backend's Pydantic models are the source of truth; display-only fallbacks
 * belong in UI helpers such as `documentDisplayName`.
 */

export type Category = "legislative_qa" | "public_opinion" | "bei_can";
export const CATEGORY_VALUES: readonly Category[] = [
    "legislative_qa",
    "public_opinion",
    "bei_can",
];

export const CATEGORY_LABELS: Record<Category, string> = {
    legislative_qa: "立院問答",
    public_opinion: "輿情",
    bei_can: "備參",
};

export const MAX_DOCUMENTS = 20;
export const MAX_QUESTION_LENGTH = 20_000;

/** Mirrors the source formats currently usable by the slide worker. */
export const SUPPORTED_SLIDE_EXTENSIONS = [
    ".pdf",
    ".docx",
    ".md",
    ".markdown",
    ".txt",
] as const;

export type DocumentStatus =
    | "queued"
    | "indexing"
    | "ready"
    | "failed"
    | "deleting"
    | "delete_failed";

/** UUIDs are represented as strings in browser code. */
export interface DocumentRead {
    id: string;
    original_filename: string;
    display_name: string;
    mime_type: string;
    extension: string;
    size_bytes: number;
    checksum: string;
    category: Category;
    folder_id: string | null;
    retrieval_enabled: boolean;
    status: DocumentStatus;
    stage: string | null;
    error: string | null;
    source_priority: number | null;
    roles: string[];
    page_count: number | null;
    table_count: number | null;
    created_at: string;
    updated_at: string;
}

/** Display-only fallback; neither field is renamed in the transport contract. */
export function documentDisplayName(
    document: Pick<DocumentRead, "display_name" | "original_filename">,
): string {
    return document.display_name || document.original_filename;
}

export function supportsSlideGeneration(
    document: Pick<DocumentRead, "extension">,
): boolean {
    const extension = document.extension.toLowerCase();
    return (SUPPORTED_SLIDE_EXTENSIONS as readonly string[]).includes(
        extension.startsWith(".") ? extension : `.${extension}`,
    );
}

export interface DocumentUpdate {
    display_name?: string | null;
    category?: Category | null;
    folder_id?: string | null;
    retrieval_enabled?: boolean | null;
}

export interface DocumentListResponse {
    items: DocumentRead[];
    total: number;
}

export interface DocumentUploadResponse extends DocumentRead {
    ingestion_job_id: string;
}

export interface FolderRead {
    id: string;
    name: string;
    created_at: string;
    updated_at: string;
}

interface FolderCreate {
    name: string;
}

type FolderUpdate = FolderCreate;

export interface IngestionJobRead {
    id: string;
    document_id: string;
    status: DocumentStatus;
    attempts: number;
    stage: string | null;
    error: string | null;
    created_at: string;
    updated_at: string;
}

export interface QaModeInfo {
    mode: Category;
    label: string;
    description: string;
}

/** Sanitized readiness contract for the application's retrieval index. */
export type RetrievalIndexState =
    | "uninitialized"
    | "provisioning"
    | "ready"
    | "error";

export type RetrievalIndexErrorCode =
    | "invalid_seed"
    | "provider_unavailable"
    | "provider_not_configured"
    | "store_missing_or_expired"
    | (string & {});

export interface RetrievalStatus {
    state: RetrievalIndexState;
    can_retrieve: boolean;
    ready_document_count: number;
    error_code?: RetrievalIndexErrorCode | null;
    warning_code?: string | null;
}

export interface Citation {
    type: "file_citation";
    text: string;
    filename: string | null;
    document_id: string | null;
    file_id: string | null;
    start_index: number | null;
    end_index: number | null;
    page: number | null;
}

export interface ChatRequest {
    question: string;
    mode: Category;
    document_ids?: string[];
    max_num_results?: number;
    include_search_results?: boolean;
}

/** Stable, workflow-facing lifecycle phases. Details stay server-side. */
export type AgentJobPhase =
    | "queued"
    | "preparing"
    | "extracting"
    | "drafting"
    | "validating"
    | "reviewing"
    | "revising"
    | "publishing"
    | "completed"
    | "failed";

/** Sanitized developer telemetry returned by the gated agent-run endpoints. */
export type AgentEventType =
    | "run_started"
    | "phase_changed"
    | "node_started"
    | "node_progress"
    | "node_completed"
    | "node_failed"
    | "heartbeat"
    | "run_completed"
    | "run_failed"
    | (string & {});

export type AgentRunStatus = "queued" | "running" | "completed" | "failed" | (string & {});
export type AgentNodeStatus = "pending" | "waiting" | "running" | "completed" | "failed" | (string & {});

export interface AgentRunSummary {
    run_id: string;
    workflow: string;
    status: AgentRunStatus;
    phase: AgentJobPhase | string | null;
    runner: string | null;
    task_id: string | null;
    worker_id: string | null;
    started_at: string | null;
    updated_at: string | null;
    finished_at: string | null;
    duration_ms: number | null;
    message: string | null;
    last_heartbeat_at: string | null;
    last_sequence: number;
}

export interface AgentNodeSnapshot {
    node_id: string;
    agent_role: string | null;
    runner: string | null;
    model: string | null;
    reasoning_effort: string | null;
    status: AgentNodeStatus;
    attempt: number | null;
    task_id: string | null;
    worker_id: string | null;
    provider_run_id: string | null;
    started_at: string | null;
    updated_at: string | null;
    finished_at: string | null;
    duration_ms: number | null;
    message: string | null;
    last_heartbeat_at: string | null;
}

export interface AgentRunSnapshot extends AgentRunSummary {
    nodes: AgentNodeSnapshot[];
}

export interface AgentEvent {
    run_id: string;
    workflow: string;
    node_id: string | null;
    agent_role: string | null;
    runner: string | null;
    model: string | null;
    reasoning_effort: string | null;
    worker_id: string | null;
    attempt: number | null;
    sequence: number;
    event_type: AgentEventType;
    status: string | null;
    phase: AgentJobPhase | string | null;
    message: string | null;
    occurred_at: string;
    duration_ms: number | null;
    metadata: Record<string, string | number | boolean | null>;
}

export interface AgentRunListResponse {
    runs: AgentRunSnapshot[];
}

export interface AgentEventListResponse {
    events: AgentEvent[];
    after: number;
    next_after: number | null;
}

export type SlideJobStatus = "queued" | "running" | "completed" | "failed";

export interface CreateSlidesJobResponse {
    job_id: string;
    status: SlideJobStatus;
    phase?: AgentJobPhase;
}

/** Request accepted by `POST /slides/jobs`. */
export interface CreateSlidePayload {
    title: string;
    document_ids: string[];
    slides_count: number;
    guidance: string;
    tone: "formal" | "casual";
}

export type SlideJobBrief = CreateSlidePayload;

export interface SlidesJobStatusResponse {
    job_id: string;
    status: SlideJobStatus;
    phase: AgentJobPhase;
    stage: string | null;
    message: string | null;
    started_at: string | null;
    finished_at: string | null;
    error: string | null;
    download_url: string | null;
    brief?: SlideJobBrief | null;
}

/** Existing UI callers use this name for the polling response. */
export type SlideJob = SlidesJobStatusResponse;

export interface CreateNewsPayload {
    document_ids: string[];
    guidance: string;
}

export interface CreateNewsJobResponse {
    job_id: string;
    status: SlideJobStatus;
    phase: AgentJobPhase;
}

export interface NewsJob {
    job_id: string;
    status: SlideJobStatus;
    phase: AgentJobPhase;
    message: string | null;
    started_at: string | null;
    finished_at: string | null;
    error: string | null;
    article: string | null;
}

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

const API_ROOT = "/api/v1";
const DEFAULT_ERROR_MESSAGE = "請求失敗，請稍後再試。";

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
    return typeof value === "object" && value !== null;
}

function stringValue(value: unknown): string | undefined {
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function rawStringValue(value: unknown): string | undefined {
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

async function readResponseBody(response: Response): Promise<unknown> {
    const text = await response.text();
    if (!text.trim()) return undefined;
    try {
        return JSON.parse(text) as unknown;
    } catch {
        return text;
    }
}

function errorCode(payload: unknown): string | undefined {
    if (!isRecord(payload)) return undefined;
    return (
        stringValue(payload.code) ??
        (isRecord(payload.detail) ? stringValue(payload.detail.code) : undefined)
    );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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

function queryString(values: object): string {
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

export interface DocumentListFilters {
    category?: Category;
    folder_id?: string | null;
    status?: DocumentStatus;
    retrieval_enabled?: boolean;
    q?: string;
}

export async function fetchDocumentList(
    filters: DocumentListFilters = {},
): Promise<DocumentListResponse> {
    return request<DocumentListResponse>(`/documents${queryString(filters)}`);
}

/** Convenience projection for list views; the response contract remains typed above. */
export async function fetchDocuments(
    filters: DocumentListFilters = {},
): Promise<DocumentRead[]> {
    return (await fetchDocumentList(filters)).items;
}

export async function getDocument(id: string): Promise<DocumentRead> {
    return request<DocumentRead>(`/documents/${encodeURIComponent(id)}`);
}

export async function uploadDocument(
    file: File,
    category: Category,
    folderId?: string | null,
): Promise<DocumentUploadResponse> {
    const form = new FormData();
    form.set("file", file);
    form.set("category", category);
    if (folderId) form.set("folder_id", folderId);
    return request<DocumentUploadResponse>("/documents", {
        method: "POST",
        body: form,
    });
}

export async function fetchFolders(): Promise<FolderRead[]> {
    return request<FolderRead[]>("/documents/folders");
}

export async function createFolder(name: string): Promise<FolderRead> {
    return request<FolderRead>("/documents/folders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name } satisfies FolderCreate),
    });
}

export async function renameFolder(id: string, name: string): Promise<FolderRead> {
    return request<FolderRead>(`/documents/folders/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name } satisfies FolderUpdate),
    });
}

export async function deleteFolder(id: string): Promise<void> {
    await request<void>(`/documents/folders/${encodeURIComponent(id)}`, {
        method: "DELETE",
    });
}

export async function updateDocument(
    id: string,
    update: DocumentUpdate,
): Promise<DocumentRead> {
    return request<DocumentRead>(`/documents/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(update),
    });
}

export async function deleteDocument(id: string): Promise<DocumentRead> {
    return request<DocumentRead>(`/documents/${encodeURIComponent(id)}`, {
        method: "DELETE",
    });
}

export async function getIngestionStatus(id: string): Promise<IngestionJobRead> {
    return request<IngestionJobRead>(
        `/documents/${encodeURIComponent(id)}/ingestion`,
    );
}

export function getDocumentDownloadUrl(id: string): string {
    return `${API_ROOT}/documents/${encodeURIComponent(id)}/download`;
}

export async function fetchQaModes(): Promise<QaModeInfo[]> {
    return request<QaModeInfo[]>("/qa-modes");
}

export async function fetchRetrievalStatus(
    options: { signal?: AbortSignal } = {},
): Promise<RetrievalStatus> {
    return request<RetrievalStatus>("/retrieval/status", options);
}

export interface StreamHandlers {
    onDelta: (text: string) => void;
    onStatus?: (phase: ChatStatusPhase) => void;
    onDone?: (citations: Citation[]) => void;
}

export type ChatStatusPhase = "preparing" | "searching" | "drafting" | "validating";

export type ChatStreamEvent =
    | { type: "status"; phase: ChatStatusPhase }
    | { type: "text_delta"; text: string }
    | { type: "done"; citations: Citation[]; mode?: Category; grounded?: boolean }
    | { type: "error"; message: string; code?: string };

function citation(value: unknown): Citation | null {
    if (
        !isRecord(value) ||
        value.type !== "file_citation" ||
        typeof value.text !== "string"
    ) {
        return null;
    }
    return {
        type: "file_citation",
        text: value.text,
        filename: typeof value.filename === "string" ? value.filename : null,
        document_id: typeof value.document_id === "string" ? value.document_id : null,
        file_id: typeof value.file_id === "string" ? value.file_id : null,
        start_index: typeof value.start_index === "number" ? value.start_index : null,
        end_index: typeof value.end_index === "number" ? value.end_index : null,
        page: typeof value.page === "number" ? value.page : null,
    };
}

/**
 * Parses one complete SSE event. Exported so stream behavior can be tested
 * without constructing a ReadableStream or relying on browser fetch.
 */
export function parseSseEventBlock(block: string): ChatStreamEvent | null {
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
        throw new ApiError(502, "對話串流格式無效。", {
            code: "chat_stream_invalid",
        });
    }
    if (!isRecord(payload)) {
        throw new ApiError(502, "對話串流格式無效。", {
            code: "chat_stream_invalid",
        });
    }
    const type = stringValue(payload.type) ?? eventName;
    if (type === "status") {
        const phase = stringValue(payload.phase);
        if (
            phase === "preparing" ||
            phase === "searching" ||
            phase === "drafting" ||
            phase === "validating"
        ) {
            return { type: "status", phase };
        }
        throw new ApiError(502, "對話串流格式無效。", {
            code: "chat_stream_invalid",
        });
    }
    if (type === "error") {
        const message =
            stringValue(payload.message) ??
            getApiErrorMessage(payload, "對話服務發生錯誤。");
        return {
            type: "error",
            message,
            code: stringValue(payload.code),
        };
    }
    if (type === "done") {
        const citations = Array.isArray(payload.citations)
            ? payload.citations
                  .map(citation)
                  .filter((item): item is Citation => item !== null)
            : [];
        return {
            type: "done",
            citations,
            mode: payload.mode as Category | undefined,
            grounded:
                typeof payload.grounded === "boolean" ? payload.grounded : undefined,
        };
    }
    if (type === "text_delta" || type === "message") {
        const text =
            rawStringValue(payload.text) ??
            rawStringValue(payload.text_delta) ??
            rawStringValue(payload.delta);
        if (text !== undefined) return { type: "text_delta", text };
        throw new ApiError(502, "對話串流格式無效。", {
            code: "chat_stream_invalid",
        });
    }
    return null;
}

export async function streamChat(
    payload: ChatRequest,
    handlers: StreamHandlers,
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
        response = await fetch(`${API_ROOT}/chat/stream`, {
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
        let finished = false;

        const consume = (block: string) => {
            const event = parseSseEventBlock(block);
            if (!event) return;
            if (event.type === "status") handlers.onStatus?.(event.phase);
            else if (event.type === "text_delta") handlers.onDelta(event.text);
            else if (event.type === "done") {
                finished = true;
                handlers.onDone?.(event.citations);
            } else {
                throw new ApiError(503, event.message, { code: event.code });
            }
        };

        while (!finished) {
            const { value, done } = await reader.read();
            if (value?.byteLength) resetInactivityTimer();
            buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
            let boundary: RegExpExecArray | null;
            while ((boundary = /\r?\n\r?\n/.exec(buffer)) !== null) {
                consume(buffer.slice(0, boundary.index));
                buffer = buffer.slice(boundary.index + boundary[0].length);
                if (finished) break;
            }
            if (done) break;
        }
        buffer += decoder.decode();
        if (!finished && buffer.trim()) consume(buffer);
        if (!finished) {
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

export async function createSlideJob(
    payload: CreateSlidePayload,
    options: { signal?: AbortSignal } = {},
): Promise<CreateSlidesJobResponse> {
    return request<CreateSlidesJobResponse>("/slides/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: options.signal,
    });
}

export async function getSlideJob(
    id: string,
    options: { signal?: AbortSignal } = {},
): Promise<SlideJob> {
    return request<SlideJob>(`/slides/jobs/${encodeURIComponent(id)}`, options);
}

export async function createNewsJob(
    payload: CreateNewsPayload,
    options: { signal?: AbortSignal } = {},
): Promise<CreateNewsJobResponse> {
    return request<CreateNewsJobResponse>("/news/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: options.signal,
    });
}

export async function getNewsJob(
    id: string,
    options: { signal?: AbortSignal } = {},
): Promise<NewsJob> {
    return request<NewsJob>(`/news/jobs/${encodeURIComponent(id)}`, options);
}

export async function fetchAgentRuns(
    limit = 50,
    options: { signal?: AbortSignal } = {},
): Promise<AgentRunListResponse> {
    return request<AgentRunListResponse>(`/dev/agent-runs${queryString({ limit })}`, options);
}

export async function fetchAgentRun(
    runId: string,
    options: { signal?: AbortSignal } = {},
): Promise<AgentRunSnapshot> {
    return request<AgentRunSnapshot>(
        `/dev/agent-runs/${encodeURIComponent(runId)}`,
        options,
    );
}

export async function fetchAgentRunEvents(
    runId: string,
    params: { after?: number; limit?: number; signal?: AbortSignal } = {},
): Promise<AgentEventListResponse> {
    const { signal, ...query } = params;
    return request<AgentEventListResponse>(
        `/dev/agent-runs/${encodeURIComponent(runId)}/events${queryString(query)}`,
        { signal },
    );
}

function getSlideDownloadUrl(id: string): string {
    return `${API_ROOT}/slides/jobs/${encodeURIComponent(id)}/download`;
}

/** Uses a server-provided URL when available, otherwise derives the API URL. */
export function slideDownloadUrl(
    job: Pick<SlideJob, "job_id" | "download_url" | "status">,
): string | null {
    return job.download_url ??
        (job.status === "completed" ? getSlideDownloadUrl(job.job_id) : null);
}
