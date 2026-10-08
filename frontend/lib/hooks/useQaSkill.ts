"use client";

import { useCallback, useRef, useState } from "react";
import type { MutableRefObject } from "react";
import { fetchChatSession, linkChatDocuments, unlinkChatDocument } from "../api/chat";
import type { ChatAttachment, ChatSessionDetail } from "../api/chat";
import {
    LEGISLATIVE_QA_SKILL,
    confirmAllQaOutlines,
    confirmQaDocuments,
    confirmQaOutline,
    fetchQaWorkspace,
    putChatSkill,
    putQaOutline,
    putQaQuestions,
} from "../api/qa";
import type { QaOutlineInput, QaQuestionInput, QaWorkspace } from "../api/qa";

/**
 * The 立院QA mode of one conversation: whether the skill is on, the live
 * workspace, and the session's attachments (candidate ticks read from these).
 * Card actions throw `ApiError`; the card that triggered one shows its message.
 */
export function useQaSkill(activeSessionId: MutableRefObject<string | null>, ensureSession: () => Promise<string>) {
    const [skill, setSkill] = useState<string | null>(null);
    const [workspace, setWorkspace] = useState<QaWorkspace | null>(null);
    const [sessionAttachments, setSessionAttachments] = useState<ChatAttachment[]>([]);
    const skillRef = useRef<string | null>(null);

    const applySkill = useCallback((value: string | null) => {
        skillRef.current = value;
        setSkill(value);
    }, []);

    const reset = useCallback(() => {
        applySkill(null);
        setWorkspace(null);
        setSessionAttachments([]);
    }, [applySkill]);

    /** Re-reads the workspace and the session's attachments; a no-op outside skill mode. */
    const refresh = useCallback(async () => {
        const id = activeSessionId.current;
        if (!id || !skillRef.current) return;
        try {
            const [next, detail] = await Promise.all([fetchQaWorkspace(id), fetchChatSession(id)]);
            if (activeSessionId.current !== id) return;
            setWorkspace(next);
            setSessionAttachments(detail.attachments ?? []);
        } catch {
            // The cards keep their last state; the next action refreshes again.
        }
    }, [activeSessionId]);

    const hydrate = useCallback((detail: ChatSessionDetail) => {
        applySkill(detail.skill ?? null);
        setSessionAttachments(detail.attachments ?? []);
        if (detail.skill) void refresh();
    }, [applySkill, refresh]);

    const enable = useCallback(async () => {
        const id = await ensureSession();
        await putChatSkill(id, LEGISLATIVE_QA_SKILL);
        applySkill(LEGISLATIVE_QA_SKILL);
        await refresh();
    }, [applySkill, ensureSession, refresh]);

    const disable = useCallback(async () => {
        const id = activeSessionId.current;
        if (!id) return;
        await putChatSkill(id, null);
        applySkill(null);
    }, [activeSessionId, applySkill]);

    /** Runs a workspace mutation for the active session, then syncs the workspace. */
    const act = useCallback(async (run: (id: string) => Promise<QaWorkspace>) => {
        const id = activeSessionId.current;
        if (!id) return;
        setWorkspace(await run(id));
    }, [activeSessionId]);

    const saveQuestions = useCallback((items: QaQuestionInput[], confirm = true) =>
        act((id) => putQaQuestions(id, items, confirm)), [act]);
    const saveOutline = useCallback((no: number, body: QaOutlineInput) =>
        act((id) => putQaOutline(id, no, body)), [act]);
    const confirmOutline = useCallback((no: number) => act((id) => confirmQaOutline(id, no)), [act]);
    const confirmAllOutlines = useCallback(() => act((id) => confirmAllQaOutlines(id)), [act]);

    /** Confirms the kept session attachments (uploads and knowledge-base documents alike). */
    const confirmDocuments = useCallback(async () => {
        const id = activeSessionId.current;
        if (!id) return;
        const ids = sessionAttachments.map((item) => item.id);
        setWorkspace(await confirmQaDocuments(id, ids));
    }, [activeSessionId, sessionAttachments]);

    /** Ticks (attach) or unticks (unlink) one knowledge-base candidate. */
    const toggleDocument = useCallback(async (documentId: string, attach: boolean) => {
        const id = activeSessionId.current;
        if (!id) return;
        if (attach) await linkChatDocuments(id, [documentId]);
        else await unlinkChatDocument(id, documentId);
        await refresh();
    }, [activeSessionId, refresh]);

    return {
        skill,
        workspace,
        sessionAttachments,
        reset,
        hydrate,
        refresh,
        enable,
        disable,
        saveQuestions,
        saveOutline,
        confirmOutline,
        confirmAllOutlines,
        confirmDocuments,
        toggleDocument,
    };
}
