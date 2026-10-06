"use client";

import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import {
    Category,
    CATEGORY_LABELS,
    CATEGORY_VALUES,
    DocumentRead,
    MAX_DOCUMENTS,
    QaModeInfo,
    getDocumentDownloadUrl,
} from "../api/documents";
import { createSlideJob, supportsSlideGeneration } from "../api/slides";
import type { CreateSlidesJobResponse } from "../api/slides";
import { useDocuments } from "./useDocuments";
import { useRetrievalStatus } from "./useRetrievalStatus";
import { useWorkspaceSession } from "./useWorkspaceSession";

// Categories are fixed in the frontend; there is no server endpoint for them.
const modes: QaModeInfo[] = CATEGORY_VALUES.map((mode) => ({
    mode,
    label: CATEGORY_LABELS[mode],
    description: "",
}));

type DocumentMutation = {
    display_name?: string;
    category?: Category;
    folder_id?: string | null;
    retrieval_enabled?: boolean;
};

function errorText(error: unknown): string {
    return error instanceof ApiError ? error.message : "服務暫時無法使用，請稍後再試。";
}

/**
 * Owns the cross-view workspace state and actions. Views stay presentational;
 * this controller is the single place where navigation, selection, transport
 * calls, and asynchronous workflow state are coordinated.
 *
 * Chat lives outside this controller: it owns its own sessions against the
 * `/chat/*` API (see `useChatSession`), so it stays a sibling route rather
 * than another view of this shared workspace state.
 */
export function useWorkspaceController() {
    const catalog = useDocuments();
    const retrieval = useRetrievalStatus();
    const [slideSubmitting, setSlideSubmitting] = useState(false);
    const session = useWorkspaceSession();
    const {
        view,
        setView,
        selected,
        setSelected,
        folderId,
        setFolderId,
        query,
        setQuery,
        uploadOpen,
        setUploadOpen,
        pending,
        setPending,
        uploadCategory,
        setUploadCategory,
        uploadFolderId,
        setUploadFolderId,
        slideTitle,
        setSlideTitle,
        slideCount,
        setSlideCount,
        guidance,
        setGuidance,
        tone,
        setTone,
    } = session;

    const [uploading, setUploading] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [pendingDelete, setPendingDelete] = useState<DocumentRead | null>(null);

    useEffect(() => {
        if (!catalog.documents.some((doc) => ["queued", "indexing", "deleting"].includes(doc.status))) return;
        const timer = window.setTimeout(() => void catalog.reloadDocuments(), 2500);
        return () => window.clearTimeout(timer);
    }, [catalog.documents, catalog.reloadDocuments]);

    const folderName = folderId
        ? catalog.folders.find((folder) => folder.id === folderId)?.name || "資料夾"
        : "全部文件";
    const selectedDocs = useMemo(
        () => catalog.documents.filter((doc) => selected.includes(doc.id)),
        [catalog.documents, selected],
    );
    const readyDocuments = useMemo(
        () => catalog.documents.filter((doc) => doc.status === "ready" && doc.retrieval_enabled),
        [catalog.documents],
    );
    const hasPendingDocuments = useMemo(
        () => catalog.documents.some((doc) => ["queued", "indexing"].includes(doc.status)),
        [catalog.documents],
    );
    const hasAnyReadyDocuments =
        readyDocuments.length > 0 ||
        (retrieval.status?.ready_document_count ?? 0) > 0;

    const toggleSelected = (id: string) => {
        setSelected((items) => {
            if (items.includes(id)) return items.filter((item) => item !== id);
            if (items.length >= MAX_DOCUMENTS) {
                setActionError("最多只能選取 " + MAX_DOCUMENTS + " 份文件。");
                return items;
            }
            return [...items, id];
        });
    };

    const openUpload = () => {
        setUploadOpen(true);
    };

    const mutateDocument = async (id: string, changes: DocumentMutation) => {
        setActionError(null);
        try {
            await catalog.updateDocument(id, changes);
        } catch (error) {
            setActionError(errorText(error));
        }
    };

    const deleteDocument = (document: DocumentRead) => {
        if (["queued", "indexing"].includes(document.status)) {
            setActionError("文件仍在索引中，完成後才能刪除。");
            return;
        }
        if (document.status !== "delete_failed") {
            setPendingDelete(document);
            return;
        }
        void performDelete(document);
    };

    const confirmDeleteDocument = () => {
        const document = pendingDelete;
        setPendingDelete(null);
        if (document) void performDelete(document);
    };

    const performDelete = (document: DocumentRead) => {
        void (async () => {
            setActionError(null);
            try {
                await catalog.deleteDocument(document.id);
                setSelected((items) => items.filter((id) => id !== document.id));
            } catch (error) {
                setActionError(errorText(error));
            }
        })();
    };

    const downloadDocument = (document: DocumentRead) => {
        const anchor = window.document.createElement("a");
        anchor.href = getDocumentDownloadUrl(document.id);
        anchor.download = document.original_filename;
        anchor.click();
    };

    const createFolder = async (name: string) => {
        try { await catalog.createFolder(name); } catch (error) { setActionError(errorText(error)); throw error; }
    };

    const renameFolder = async (id: string, name: string) => {
        try { await catalog.renameFolder(id, name); } catch (error) { setActionError(errorText(error)); throw error; }
    };

    const deleteFolder = async (id: string) => {
        try {
            await catalog.deleteFolder(id);
            if (folderId === id) setFolderId(null);
        } catch (error) { setActionError(errorText(error)); throw error; }
    };

    const upload = async () => {
        if (!pending.length || uploading) return;
        setUploading(true);
        try {
            for (const file of pending) await catalog.upload(file, uploadCategory, uploadFolderId);
            setPending([]);
            setUploadOpen(false);
            await catalog.reloadDocuments();
            await retrieval.reload();
        } catch (error) {
            setActionError(errorText(error));
        } finally {
            setUploading(false);
        }
    };

    const startSlides = async (): Promise<CreateSlidesJobResponse | null> => {
        const eligible = selectedDocs.filter((doc) => doc.status === "ready" && supportsSlideGeneration(doc));
        if (!eligible.length || !slideTitle.trim() || slideSubmitting) return null;
        setActionError(null);
        setSlideSubmitting(true);
        try {
            return await createSlideJob({
                title: slideTitle.trim(),
                document_ids: eligible.map((doc) => doc.id),
                slides_count: slideCount,
                guidance,
                tone,
            });
        } catch (error) {
            setActionError(errorText(error));
            return null;
        } finally {
            setSlideSubmitting(false);
        }
    };

    return {
        catalog,
        slideSubmitting,
        view,
        setView,
        selected,
        setSelected,
        selectedDocs,
        folderId,
        setFolderId,
        folderName,
        query,
        setQuery,
        uploadOpen,
        setUploadOpen,
        pending,
        setPending,
        uploadCategory,
        setUploadCategory,
        uploadFolderId,
        setUploadFolderId,
        uploading,
        openUpload,
        modes,
        actionError,
        retrievalStatus: retrieval.status,
        retrievalLoading: retrieval.loading,
        retrievalError: retrieval.error?.message || null,
        reloadRetrieval: retrieval.reload,
        catalogLoading: catalog.loadingDocuments,
        hasPendingDocuments,
        hasAnyReadyDocuments,
        toggleSelected,
        mutateDocument,
        deleteDocument,
        pendingDelete,
        cancelDeleteDocument: () => setPendingDelete(null),
        confirmDeleteDocument,
        downloadDocument,
        createFolder,
        renameFolder,
        deleteFolder,
        upload,
        slideTitle,
        setSlideTitle,
        slideCount,
        setSlideCount,
        guidance,
        setGuidance,
        tone,
        setTone,
        startSlides,
    };
}

export type WorkspaceController = ReturnType<typeof useWorkspaceController>;
