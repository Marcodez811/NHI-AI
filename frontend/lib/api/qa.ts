/** 立院QA skill: session skill mode, the QA workspace, and the cards streamed into chat. */
import { isRecord, request } from "./client";
import type { ChatSessionSummary } from "./chat";

export type ChatSkill = "legislative_qa";
export const LEGISLATIVE_QA_SKILL: ChatSkill = "legislative_qa";

export type QaStage = "questions" | "documents" | "evidence" | "outline" | "ready";
export const QA_STAGES: readonly QaStage[] = ["questions", "documents", "evidence", "outline", "ready"];

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
    confirmed: boolean;
}

export interface QaWorkspace {
    stage: QaStage;
    questions: { items: QaQuestion[]; confirmed: boolean };
    documents: { confirmed: boolean; used: QaUsedDocument[] };
    evidence: Record<string, QaEvidence>;
    outline: Record<string, QaOutline>;
    versions?: unknown[];
    base_version_id?: string | null;
    updated_at?: string;
}

export interface QaOutlineInput {
    short: string[];
    detail?: QaDetailSection[];
    dispute_requested?: boolean;
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
