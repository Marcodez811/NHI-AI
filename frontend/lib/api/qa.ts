/** 立院QA skill: session skill mode, the QA workspace, and the cards streamed into chat. */
import { isRecord, request } from "./client";
import type { ChatSessionSummary } from "./chat";

export type ChatSkill = "legislative_qa";
export const LEGISLATIVE_QA_SKILL: ChatSkill = "legislative_qa";

export type QaStage = "questions" | "documents" | "evidence" | "outline" | "ready" | "generating";
export const QA_STAGES: readonly QaStage[] = ["questions", "documents", "evidence", "outline", "ready", "generating"];

export interface QaQuestion {
    no: number;
    text: string;
    note?: string;
}

export interface QaQuestionInput {
    no?: number;
    text: string;
    note?: string;
}

export interface QaUsedDocument {
    id: string;
    name: string;
    source: "upload" | "knowledge_base";
}

export interface QaEvidenceItem {
    text: string;
    source: { id: string; name: string };
    quote: string;
}

export const QA_EVIDENCE_GROUPS = ["figures", "aim", "status", "dispute", "next_steps"] as const;
export type QaEvidenceGroup = (typeof QA_EVIDENCE_GROUPS)[number];
export type QaEvidence = Record<QaEvidenceGroup, QaEvidenceItem[]>;

export interface QaDetailSection {
    title: string;
    points: string[];
}

export interface QaOutline {
    short: string[];
    detail: QaDetailSection[];
    dispute_requested: boolean;
    notes?: string[];
    confirmed: boolean;
}

export type QaIssueKind = "numeric" | "inconsistent" | "unsupported" | "off_question" | "outline" | "style";

/** A verification finding the writer left unresolved; shown as ⚠ for manual confirmation. */
export interface QaIssue {
    question_no: number;
    part: "short" | "detail";
    kind: QaIssueKind;
    message: string;
    /** 0-based place inside the part; absent when the finding is about the whole part. */
    section?: number | null;
    paragraph?: number | null;
}

/** One produced Word file, newest first in `QaWorkspace.versions`. */
export interface QaVersion {
    /** "ai" = written by the AI writer; "outline" (or absent) = the deterministic outline wording. */
    kind?: "ai" | "outline";
    artifact_id: string;
    title: string;
    created_at: string;
    download_url: string;
    /** AI versions only: true once the document was verified; `issues` are then the unresolved findings. */
    checked?: boolean;
    issues?: QaIssue[];
    /** Scoped revisions only: the base version's artifact id and the user's instruction. */
    revised_from?: string | null;
    revision_note?: string | null;
}

export interface QaDocParagraph {
    text: string;
    /** 1-based indexes into `QaDocument.sources`. */
    cites: number[];
}
export interface QaDocSection {
    heading: string | null;
    paragraphs: QaDocParagraph[];
}
export interface QaDocQuestion {
    no: number;
    text: string;
    short: QaDocSection[];
    detail: QaDocSection[];
}
/** The structured content one version's Word file is built from. */
export interface QaDocument {
    kind: "ai" | "outline";
    title: string;
    session_label?: string | null;
    questions: QaDocQuestion[];
    sources: string[];
    checked: boolean;
    issues: QaIssue[];
    revised_from?: string | null;
    revision_note?: string | null;
}
export interface QaVersionContent {
    artifact_id: string;
    kind: "ai" | "outline";
    created_at?: string | null;
    download_url: string;
    document: QaDocument;
}

export type QaPart = "short" | "detail" | "both";
export interface QaRevisionInput {
    question_no: number;
    part: QaPart;
    section?: number | null;
    instruction: string;
    /** Defaults to the latest AI version on the server. */
    base_artifact_id?: string;
}

export interface QaDocumentResult {
    id: string;
    title: string;
    download_url: string;
}

export interface QaWorkspace {
    stage: QaStage;
    questions: { items: QaQuestion[]; confirmed: boolean };
    documents: { confirmed: boolean; used: QaUsedDocument[] };
    evidence: Record<string, QaEvidence>;
    outline: Record<string, QaOutline>;
    versions?: QaVersion[];
    /** The running AI-writing job, empty when idle. */
    generation?: {
        job_id?: string;
        session_label?: string | null;
        started_at?: string;
        revision?: { base_artifact_id: string; question_no: number; part: QaPart };
    };
    base_version_id?: string | null;
    updated_at?: string;
}

export interface QaOutlineInput {
    short: string[];
    detail?: QaDetailSection[];
    dispute_requested?: boolean;
    notes?: string[];
    confirm?: boolean;
}

export type QaCardKind = "questions" | "documents" | "evidence" | "outline";
const QA_CARD_KINDS: readonly string[] = ["questions", "documents", "evidence", "outline"];

/** A snapshot of one workspace step; `data` shapes are in the card components. */
export interface QaCard {
    kind: QaCardKind;
    data: Record<string, unknown>;
}

export interface QaQuestionsCardData {
    items: QaQuestion[];
    confirmed: boolean;
}
export interface QaDocumentsCardData {
    items: Array<{
        question_no: number;
        question_text: string;
        candidates: Array<{ id: string; name: string; snippet: string; attached: boolean }>;
    }>;
    confirmed: boolean;
}
export interface QaEvidenceCardData {
    question_no: number;
    question_text: string;
    evidence: QaEvidence;
}
export interface QaOutlineCardData {
    question_no: number;
    question_text: string;
    outline: QaOutline;
}

/** Validates one card from SSE or a persisted message; anything unrecognized yields null. */
export function parseQaCard(value: unknown): QaCard | null {
    if (!isRecord(value) || typeof value.kind !== "string" || !QA_CARD_KINDS.includes(value.kind)) return null;
    if (!isRecord(value.data)) return null;
    return { kind: value.kind as QaCardKind, data: value.data };
}

export function parseQaCards(value: unknown): QaCard[] {
    return Array.isArray(value)
        ? value.map(parseQaCard).filter((card): card is QaCard => card !== null)
        : [];
}

const json = (body: unknown): RequestInit => ({
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
});

const qaPath = (sessionId: string, tail = "") => `/chat/sessions/${encodeURIComponent(sessionId)}/qa${tail}`;

export function putChatSkill(sessionId: string, skill: ChatSkill | null): Promise<ChatSessionSummary> {
    return request<ChatSessionSummary>(`/chat/sessions/${encodeURIComponent(sessionId)}/skill`, {
        method: "PUT",
        ...json({ skill }),
    });
}

export function fetchQaWorkspace(sessionId: string, options: { signal?: AbortSignal } = {}): Promise<QaWorkspace> {
    return request<QaWorkspace>(qaPath(sessionId), options);
}

export function putQaQuestions(
    sessionId: string,
    questions: QaQuestionInput[],
    confirm = true,
): Promise<QaWorkspace> {
    return request<QaWorkspace>(qaPath(sessionId, "/questions"), { method: "PUT", ...json({ questions, confirm }) });
}

export function confirmQaDocuments(sessionId: string, attachmentIds: string[]): Promise<QaWorkspace> {
    return request<QaWorkspace>(qaPath(sessionId, "/documents/confirm"), {
        method: "POST",
        ...json({ attachment_ids: attachmentIds }),
    });
}

export function putQaOutline(sessionId: string, no: number, body: QaOutlineInput): Promise<QaWorkspace> {
    return request<QaWorkspace>(qaPath(sessionId, `/outlines/${no}`), { method: "PUT", ...json(body) });
}

export function confirmQaOutline(sessionId: string, no: number): Promise<QaWorkspace> {
    return request<QaWorkspace>(qaPath(sessionId, `/outlines/${no}/confirm`), { method: "POST" });
}

export function confirmAllQaOutlines(sessionId: string): Promise<QaWorkspace> {
    return request<QaWorkspace>(qaPath(sessionId, "/outlines/confirm-all"), { method: "POST" });
}

/** Builds the .docx from the confirmed outlines; the server records it in the workspace versions. */
export function createQaDocument(sessionId: string, sessionLabel?: string): Promise<QaDocumentResult> {
    const label = sessionLabel?.trim();
    return request<QaDocumentResult>(qaPath(sessionId, "/document"), {
        method: "POST",
        ...json(label ? { session_label: label } : {}),
    });
}

export type QaJobStatus = "queued" | "running" | "completed" | "failed";

/** Progress of one AI-writing job (`phase` is the agent workflow phase, e.g. "drafting"). */
export interface QaGenerationJob {
    job_id: string;
    status: QaJobStatus;
    phase: string;
    message?: string | null;
    error?: string | null;
    artifact_id?: string | null;
    download_url?: string | null;
    checked?: boolean;
    issues?: QaIssue[];
}

/** Starts the AI writer for the confirmed outlines; 409 when one is already running. */
export function startQaGeneration(sessionId: string, sessionLabel?: string): Promise<QaGenerationJob> {
    const label = sessionLabel?.trim();
    return request<QaGenerationJob>(qaPath(sessionId, "/generate"), {
        method: "POST",
        ...json(label ? { session_label: label } : {}),
    });
}

export function fetchQaGeneration(sessionId: string, jobId: string): Promise<QaGenerationJob> {
    return request<QaGenerationJob>(qaPath(sessionId, `/generate/${encodeURIComponent(jobId)}`));
}

export function fetchQaVersion(sessionId: string, artifactId: string, options: { signal?: AbortSignal } = {}): Promise<QaVersionContent> {
    return request<QaVersionContent>(qaPath(sessionId, `/versions/${encodeURIComponent(artifactId)}`), options);
}

/** Starts a scoped revision of one part; 409 when a job is already running. Progress arrives via the workspace. */
export function startQaRevision(sessionId: string, body: QaRevisionInput): Promise<QaGenerationJob> {
    return request<QaGenerationJob>(qaPath(sessionId, "/revise"), { method: "POST", ...json(body) });
}
