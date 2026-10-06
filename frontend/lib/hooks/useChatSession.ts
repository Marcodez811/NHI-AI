"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError } from "../api/client";
import {
    classifyChatAttachment,
    createChatSession,
    linkChatDocuments,
    linkChatFiles,
    unlinkChatDocument,
    unlinkChatFile,
    fetchChatSession,
    sendChatMessage,
    uploadChatAttachment,
} from "../api/chat";
import type { ChatAttachment, ChatMessageRecord, ChatSourceRef } from "../api/chat";
import type { ChatLimits } from "../api/config";
import { useChatSessions } from "./useChatSessions";
import { useClientConfig } from "./useClientConfig";

function errorText(error: unknown): string {
    return error instanceof ApiError ? error.message : "服務暫時無法使用，請稍後再試。";
}

let localIdSeq = 0;
function newLocalId(prefix: string): string {
    return globalThis.crypto?.randomUUID?.() ?? `${prefix}-${Date.now()}-${++localIdSeq}`;
}

export type ToolStep = { tool: string; label: string; done: boolean };

/**
 * One turn as rendered: server-shaped fields plus client-only streaming state.
 * `localKey` is a stable identity for matching in-flight updates; `id` itself
 * is replaced once the server assigns the real message id (`message_start`).
 */
export type ChatTurn = ChatMessageRecord & {
    localKey: string;
    toolSteps?: ToolStep[];
    pending?: boolean;
    errorText?: string;
    reasoningStartedAt?: number;
    reasoningDurationSeconds?: number;
    reasoningStreaming?: boolean;
    answerStarted?: boolean;
    compacting?: boolean;
    receivedEvent?: boolean;
};

export type ComposerAttachment = {
    localId: string;
    /** Absent for files and knowledge-base documents attached from elsewhere. */
    file?: File;
    status: "uploading" | "ready" | "failed";
    attachment?: ChatAttachment;
    error?: string;
};

/** Client-side echo of the server's own validation, for instant feedback before upload. */
function validateFile(file: File, limits: ChatLimits): string | null {
    const kind = classifyChatAttachment(file);
    if (!kind) return "不支援的檔案類型，僅接受 PDF、DOCX、TXT、MD 或 PNG、JPEG、WEBP 圖片。";
    const isPdf = file.name.toLowerCase().endsWith(".pdf");
    const limit = kind === "image" ? limits.max_image_bytes : isPdf ? limits.max_pdf_bytes : limits.max_document_bytes;
    if (file.size > limit) {
        const limitMb = Math.round(limit / (1024 * 1024));
        return `檔案超過大小上限（${limitMb} MB）。`;
    }
    return null;
}

/**
 * Owns one chat conversation: loading an existing session (or starting a new
 * one), attachments, and streaming a turn through the chat v2 SSE contract.
 * The session is created lazily on first send/attach, then the caller
 * navigates to `/chat/<id>` via the router replace triggered here.
 */
export function useChatSession(sessionId: string | null, defaultModel?: string) {
    const router = useRouter();
    const sessionsCtx = useChatSessions();
    const chatLimits = useClientConfig().chat;
    const [messages, setMessages] = useState<ChatTurn[]>([]);
    const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
    const [attachmentLookup, setAttachmentLookup] = useState<Record<string, ChatAttachment>>({});
    const [draft, setDraft] = useState("");
    const [model, setModel] = useState<string | undefined>(defaultModel);
    const [loading, setLoading] = useState(Boolean(sessionId));
    const [loadError, setLoadError] = useState<string | null>(null);
    const [sendError, setSendError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [compactedThroughMessageId, setCompactedThroughMessageId] = useState<string | null>(null);
    const controllerRef = useRef<AbortController | null>(null);
    const activeSessionId = useRef<string | null>(sessionId);
    // Sessions created by `ensureSession` below, in this hook instance. When the
    // `sessionId` prop catches up to one of these (the router navigating from
    // `/chat` to `/chat/<id>` right after creation), the load effect must not
    // reset the conversation it is actively streaming into.
    const selfCreatedIds = useRef<Set<string>>(new Set());

    // A model choice already picked for a fresh conversation should not be
    // clobbered once the model list resolves its default asynchronously.
    useEffect(() => {
        if (!sessionId) setModel((current) => current ?? defaultModel);
    }, [defaultModel, sessionId]);

    useEffect(() => {
        activeSessionId.current = sessionId;
        if (sessionId && selfCreatedIds.current.has(sessionId)) {
            // The URL just caught up to a session this hook instance already
            // created and is streaming into; local state is the source of truth.
            setLoading(false);
            return;
        }
        setMessages([]);
        setAttachments([]);
        setAttachmentLookup({});
        setCompactedThroughMessageId(null);
        setLoadError(null);
        if (!sessionId) {
            setLoading(false);
            return;
        }
        let cancelled = false;
        setLoading(true);
        (async () => {
            try {
                const detail = await fetchChatSession(sessionId);
                if (cancelled) return;
                setMessages(detail.messages.map((message) => ({
                    ...message,
                    localKey: message.id,
                    reasoningStreaming: false,
                    answerStarted: Boolean(message.content),
                })));
                setAttachmentLookup(Object.fromEntries((detail.attachments ?? []).map((item) => [item.id, item])));
                setCompactedThroughMessageId(detail.compacted_through_message_id ?? null);
                setModel(detail.model);
            } catch (caught) {
                if (!cancelled) setLoadError(errorText(caught));
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [sessionId]);

    const ensureSession = useCallback(async (): Promise<string> => {
        if (activeSessionId.current) return activeSessionId.current;
        const created = await createChatSession(model);
        activeSessionId.current = created.id;
        selfCreatedIds.current.add(created.id);
        setModel(created.model);
        void sessionsCtx.refresh();
        router.replace(`/chat/${created.id}`);
        return created.id;
    }, [model, router, sessionsCtx]);

    const uploadOne = useCallback((targetId: string, file: File, localId: string): Promise<ChatAttachment | null> => {
        return uploadChatAttachment(targetId, file)
            .then((attachment) => {
                setAttachments((items) => items.map((item) => (item.localId === localId
                    ? { ...item, status: attachment.status, attachment, error: attachment.error ?? undefined }
                    : item)));
                return attachment.status === "ready" ? attachment : null;
            })
            .catch((caught) => {
                setAttachments((items) => items.map((item) => (item.localId === localId
                    ? { ...item, status: "failed", error: errorText(caught) }
                    : item)));
                return null;
            });
    }, []);

    /** Uploads files; resolves with the attachments that finished successfully. */
    const attachFiles = useCallback(async (files: File[]): Promise<ChatAttachment[]> => {
        const room = chatLimits.max_attachments - attachments.length;
        const accepted = files.slice(0, Math.max(room, 0));
        if (!accepted.length) return [];

        const targetId = await ensureSession();
        const uploads: Promise<ChatAttachment | null>[] = [];
        for (const file of accepted) {
            const localId = newLocalId("attachment");
            const invalid = validateFile(file, chatLimits);
            if (invalid) {
                setAttachments((items) => [...items, { localId, file, status: "failed", error: invalid }]);
                continue;
            }
            setAttachments((items) => [...items, { localId, file, status: "uploading" }]);
            uploads.push(uploadOne(targetId, file, localId));
        }
        const done = await Promise.all(uploads);
        return done.filter((item): item is ChatAttachment => item !== null);
    }, [attachments.length, chatLimits, ensureSession, uploadOne]);

    /** Links already-stored files or knowledge-base documents to this conversation. */
    const attachExisting = useCallback(async (
        source: "upload" | "knowledge_base",
        entries: Array<Pick<ChatAttachment, "id" | "display_name" | "mime_type" | "kind" | "size_bytes">>,
    ) => {
        const known = new Set(attachments.map((item) => item.attachment?.id));
        const fresh = entries
            .filter((entry) => !known.has(entry.id))
            .slice(0, Math.max(chatLimits.max_attachments - attachments.length, 0));
        if (!fresh.length) return;
        const targetId = await ensureSession();
        const ids = fresh.map((entry) => entry.id);
        if (source === "knowledge_base") await linkChatDocuments(targetId, ids);
        else await linkChatFiles(targetId, ids);
        setAttachments((items) => [
            ...items,
            ...fresh.map((entry) => ({
                localId: newLocalId("attachment"),
                status: "ready" as const,
                attachment: {
                    ...entry,
                    status: "ready" as const,
                    error: null,
                    text_chars: null,
                    created_at: new Date().toISOString(),
                    source,
                },
            })),
        ]);
    }, [attachments, chatLimits, ensureSession]);

    const removeAttachment = useCallback((localId: string) => {
        setAttachments((items) => {
            const target = items.find((item) => item.localId === localId);
            if (target?.attachment && activeSessionId.current) {
                // Unlink only: the file stays in "my files", the document stays in the knowledge base.
                const unlink = target.attachment.source === "knowledge_base" ? unlinkChatDocument : unlinkChatFile;
                void unlink(activeSessionId.current, target.attachment.id).catch(() => undefined);
            }
            return items.filter((item) => item.localId !== localId);
        });
    }, []);

    const uploadsPending = attachments.some((item) => item.status === "uploading");

    const send = useCallback(async () => {
        const content = draft.trim();
        if (!content || busy || uploadsPending) return;
        const attachmentIds = attachments
            .filter((item): item is ComposerAttachment & { attachment: ChatAttachment } =>
                item.status === "ready" && Boolean(item.attachment))
            .map((item) => item.attachment.id);

        setSendError(null);
        setBusy(true);
        setDraft("");
        const sentAttachments = attachments
            .map((item) => item.attachment)
            .filter((item): item is ChatAttachment => Boolean(item) && attachmentIds.includes(item!.id));
        if (sentAttachments.length) {
            setAttachmentLookup((current) => ({
                ...current,
                ...Object.fromEntries(sentAttachments.map((item) => [item.id, item])),
            }));
        }
        setAttachments([]);

        let targetId: string;
        try {
            targetId = await ensureSession();
        } catch (caught) {
            setSendError(errorText(caught));
            setBusy(false);
            return;
        }

        const userLocalId = newLocalId("user");
        const userTurn: ChatTurn = {
            id: userLocalId,
            localKey: userLocalId,
            role: "user",
            content,
            attachment_ids: attachmentIds,
            status: "complete",
            created_at: new Date().toISOString(),
        };
        const assistantLocalId = newLocalId("assistant");
        const assistantTurn: ChatTurn = {
            id: assistantLocalId,
            localKey: assistantLocalId,
            role: "assistant",
            content: "",
            status: "complete",
            created_at: new Date().toISOString(),
            toolSteps: [],
            pending: true,
            // Thinking starts when the message is sent, not when the first
            // reasoning chunk arrives: summaries often land in one burst just
            // before the answer, which would otherwise read as 0 seconds.
            reasoningStartedAt: Date.now(),
            reasoningStreaming: false,
            answerStarted: false,
            compacting: false,
            receivedEvent: false,
        };
        setMessages((items) => [...items, userTurn, assistantTurn]);

        // Matches by `localKey`, which never changes, because `onMessageStart`
        // overwrites the visible `id` with the server's real message id.
        const updateAssistant = (update: (turn: ChatTurn) => ChatTurn) => {
            setMessages((items) => items.map((item) => (item.localKey === assistantLocalId ? update(item) : item)));
        };
        const finishReasoning = (turn: ChatTurn): ChatTurn => ({
            ...turn,
            reasoningStreaming: false,
            reasoningDurationSeconds: turn.reasoningStartedAt !== undefined && turn.reasoningDurationSeconds === undefined
                ? Math.max(1, Math.round((Date.now() - turn.reasoningStartedAt) / 1000))
                : turn.reasoningDurationSeconds,
        });

        const controller = new AbortController();
        controllerRef.current = controller;
        let compacted = false;

        try {
            await sendChatMessage(
                targetId,
                { content, attachment_ids: attachmentIds, model: model ?? "" },
                {
                    onMessageStart: (messageId) => updateAssistant((turn) => ({ ...turn, id: messageId, receivedEvent: true })),
                    onReasoningDelta: (text) => updateAssistant((turn) => ({
                        ...turn,
                        reasoning: (turn.reasoning ?? "") + text,
                        reasoningStartedAt: turn.reasoningStartedAt ?? Date.now(),
                        reasoningStreaming: !turn.answerStarted,
                        receivedEvent: true,
                    })),
                    onCompacting: () => updateAssistant((turn) => ({ ...turn, compacting: true, receivedEvent: true })),
                    onCompacted: (ok) => {
                        compacted = ok;
                        updateAssistant((turn) => ({ ...turn, compacting: false, receivedEvent: true }));
                    },
                    onDelta: (text) => updateAssistant((turn) => ({
                        ...finishReasoning(turn),
                        content: turn.content + text,
                        answerStarted: true,
                        receivedEvent: true,
                    })),
                    onToolStarted: (tool, label) => updateAssistant((turn) => ({
                        ...turn,
                        receivedEvent: true,
                        toolSteps: [...(turn.toolSteps ?? []), { tool, label, done: false }],
                    })),
                    onToolFinished: (tool, label) => updateAssistant((turn) => {
                        const steps = turn.toolSteps ?? [];
                        const index = steps.findIndex((step) => step.tool === tool && !step.done);
                        if (index === -1) return { ...turn, receivedEvent: true, toolSteps: [...steps, { tool, label, done: true }] };
                        const next = steps.slice();
                        next[index] = { ...next[index], label, done: true };
                        return { ...turn, receivedEvent: true, toolSteps: next };
                    }),
                    onSources: (sources: ChatSourceRef[]) => updateAssistant((turn) => ({ ...turn, sources, receivedEvent: true })),
                    onDone: (messageId, title) => {
                        updateAssistant((turn) => ({
                            ...finishReasoning(turn), id: messageId, pending: false,
                            compacting: false, receivedEvent: true, status: "complete",
                        }));
                        if (title) void sessionsCtx.refresh();
                    },
                },
                { signal: controller.signal },
            );
        } catch (caught) {
            if (controller.signal.aborted) {
                updateAssistant((turn) => ({
                    ...finishReasoning(turn), pending: false, compacting: false, status: "interrupted",
                }));
            } else {
                const message = errorText(caught);
                setSendError(message);
                updateAssistant((turn) => ({
                    ...finishReasoning(turn), pending: false, compacting: false, status: "error", errorText: message,
                }));
            }
        } finally {
            controllerRef.current = null;
            setBusy(false);
        }
        if (compacted) {
            try {
                const detail = await fetchChatSession(targetId);
                if (activeSessionId.current === targetId) {
                    setCompactedThroughMessageId(detail.compacted_through_message_id ?? null);
                    // Earlier locally echoed user turns need their persisted ids so
                    // the compaction divider can sit beside them without a reload.
                    setMessages((items) => items.length === detail.messages.length
                        && items.every((item, index) =>
                            item.role === detail.messages[index].role && item.content === detail.messages[index].content)
                        ? items.map((item, index) => ({ ...item, id: detail.messages[index].id }))
                        : items);
                }
            } catch {
                // The stream has finished; a failed marker refresh must not discard its answer.
            }
        }
    }, [attachments, busy, draft, ensureSession, model, sessionsCtx, uploadsPending]);

    const stop = useCallback(() => {
        controllerRef.current?.abort();
    }, []);

    return {
        sessionId: activeSessionId.current,
        messages,
        compactedThroughMessageId,
        attachments,
        attachmentLookup,
        draft,
        setDraft,
        model,
        setModel,
        loading,
        loadError,
        sendError,
        busy,
        uploadsPending,
        attachFiles,
        attachExisting,
        removeAttachment,
        send,
        stop,
    };
}

export type ChatSessionState = ReturnType<typeof useChatSession>;
