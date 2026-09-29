/** Slide and news job contracts, plus the outline-planner SSE stream. */
import type { Category, DocumentRead } from "./documents";
import type { AgentJobPhase } from "./agents";
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

/** Mirrors the source formats currently usable by the slide worker. */
export const SUPPORTED_SLIDE_EXTENSIONS = [
    ".pdf",
    ".docx",
    ".md",
    ".markdown",
    ".txt",
] as const;

export function supportsSlideGeneration(
    document: Pick<DocumentRead, "extension">,
): boolean {
    const extension = document.extension.toLowerCase();
    return (SUPPORTED_SLIDE_EXTENSIONS as readonly string[]).includes(
        extension.startsWith(".") ? extension : `.${extension}`,
    );
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


export type SlideJobStatus = "queued" | "running" | "awaiting_input" | "completed" | "failed";

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

/** Fields needed to find and reopen a slide job from the index. */
export interface SlideJobSummary {
    job_id: string;
    title: string;
    status: SlideJobStatus;
    phase: AgentJobPhase;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
}

export type OutlineEmphasis = "light" | "normal" | "deep";

export interface SlideOutlineNode {
    id: string;
    heading: string;
    intent: string;
    key_points: string[];
    emphasis: OutlineEmphasis;
    approx_slides: number;
}

export interface SlideOutline {
    title: string;
    narrative: string;
    nodes: SlideOutlineNode[];
    total_slides: number;
}

export interface OutlineRevisionResponse {
    job_id: string;
    revision: number;
    outline: SlideOutline;
    session_id: string;
    created_at: string;
    approved_at: string | null;
}

export interface ApproveOutlineResponse {
    job_id: string;
    status: SlideJobStatus;
    phase: AgentJobPhase;
    approved_revision: number;
}

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

export interface StreamHandlers {
    onDelta: (text: string) => void;
    onStatus?: (phase: ChatStatusPhase) => void;
    onDone?: (citations: Citation[]) => void;
}

export type ChatStatusPhase = "preparing" | "searching" | "drafting" | "validating" | "planning";

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

const outlineTextDelta: SseEventTable<ChatStreamEvent>[string] = (payload) => {
    const text =
        rawStringValue(payload.text) ??
        rawStringValue(payload.text_delta) ??
        rawStringValue(payload.delta);
    if (text !== undefined) return { type: "text_delta", text };
    throw invalidStreamError();
};

const OUTLINE_EVENTS: SseEventTable<ChatStreamEvent> = {
    status: (payload) => {
        const phase = stringValue(payload.phase);
        if (
            phase === "preparing" ||
            phase === "searching" ||
            phase === "drafting" ||
            phase === "validating" ||
            phase === "planning"
        ) {
            return { type: "status", phase };
        }
        throw invalidStreamError();
    },
    error: (payload) => ({
        type: "error",
        message:
            stringValue(payload.message) ??
            getApiErrorMessage(payload, "對話服務發生錯誤。"),
        code: stringValue(payload.code),
    }),
    done: (payload) => ({
        type: "done",
        citations: Array.isArray(payload.citations)
            ? payload.citations
                  .map(citation)
                  .filter((item): item is Citation => item !== null)
            : [],
        mode: payload.mode as Category | undefined,
        grounded: typeof payload.grounded === "boolean" ? payload.grounded : undefined,
    }),
    text_delta: outlineTextDelta,
    message: outlineTextDelta,
};

/**
 * Parses one complete SSE event in the outline-planner vocabulary. Exported so
 * stream behavior can be tested without constructing a ReadableStream.
 */
export function parseSseEventBlock(block: string): ChatStreamEvent | null {
    return parseSseBlock(block, OUTLINE_EVENTS);
}

async function streamSse(
    path: string,
    payload: unknown,
    handlers: StreamHandlers,
    options: { signal?: AbortSignal } = {},
): Promise<void> {
    let finished = false;
    await consumeSseStream(
        path,
        payload,
        (block) => {
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
        },
        () => finished,
        options,
    );
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

export async function listSlideJobs(
    limit = 20,
    options: { signal?: AbortSignal } = {},
): Promise<SlideJobSummary[]> {
    return request<SlideJobSummary[]>(`/slides/jobs${queryString({ limit })}`, options);
}

export async function getSlideJobOutline(
    id: string,
    options: { signal?: AbortSignal } = {},
): Promise<OutlineRevisionResponse> {
    return request<OutlineRevisionResponse>(`/slides/jobs/${encodeURIComponent(id)}/outline`, options);
}

/** The planner uses the same SSE vocabulary as retrieval chat. */
export async function streamSlideJobOutlineMessage(
    id: string,
    message: string,
    handlers: StreamHandlers,
    options: { signal?: AbortSignal } = {},
): Promise<void> {
    return streamSse(
        `/slides/jobs/${encodeURIComponent(id)}/outline/messages`,
        { message },
        handlers,
        options,
    );
}

export async function approveSlideJobOutline(
    id: string,
    expectedRevision: number,
    options: { signal?: AbortSignal } = {},
): Promise<ApproveOutlineResponse> {
    return request<ApproveOutlineResponse>(`/slides/jobs/${encodeURIComponent(id)}/outline/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: expectedRevision }),
        signal: options.signal,
    });
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

