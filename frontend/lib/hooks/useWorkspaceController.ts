"use client";

import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import {
    Category,
    CATEGORY_VALUES,
    DocumentRead,
    MAX_DOCUMENTS,
    QaModeInfo,
    getDocumentDownloadUrl,
} from "../api/documents";
import { fetchQaModes, streamChat } from "../api/chat";
import { supportsSlideGeneration } from "../api/slides";
import { useDocuments } from "./useDocuments";
import { useRetrievalStatus } from "./useRetrievalStatus";
import { useSlideJob } from "./useSlideJob";
import { useWorkspaceSession } from "./useWorkspaceSession";

const fallbackModes: QaModeInfo[] = CATEGORY_VALUES.map((mode) => ({
    mode,
    label: mode,
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
 */
export function useWorkspaceController() {
    const catalog = useDocuments();
    const retrieval = useRetrievalStatus();
    const slideJob = useSlideJob();
    const session = useWorkspaceSession();
    const {
        view,
        setView,
        collapsed,
        setCollapsed,
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
        scope,
        setScope,
        chat,
        setChat,
        draft,
        setDraft,
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
    const [qaModes, setQaModes] = useState<QaModeInfo[]>([]);
    const [qaError, setQaError] = useState<string | null>(null);
    const [chatBusy, setChatBusy] = useState(false);
    const [chatError, setChatError] = useState<string | null>(null);
    const [eligibilityError, setEligibilityError] = useState<string | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);

    const loadModes = async () => {
        try {
            setQaError(null);
            setQaModes(await fetchQaModes());
        } catch (error) {
            setQaError(errorText(error));
        }
    };

    useEffect(() => {
        void loadModes();
    }, []);

    useEffect(() => {
        if (qaModes.length && !qaModes.some((mode) => mode.mode === scope)) {
            setScope(qaModes[0].mode);
        }
    }, [qaModes, scope]);

    useEffect(() => {
        if (!catalog.documents.some((doc) => ["queued", "indexing", "deleting"].includes(doc.status))) return;
        const timer = window.setTimeout(() => void catalog.reloadDocuments(), 2500);
        return () => window.clearTimeout(timer);
    }, [catalog.documents, catalog.reloadDocuments]);

    const modes = qaModes.length ? qaModes : fallbackModes;
    const folderName = folderId
        ? catalog.folders.find((folder) => folder.id === folderId)?.name || "資料夾"
        : "全部文件";
    const selectedDocs = useMemo(
        () => catalog.documents.filter((doc) => selected.includes(doc.id)),
        [catalog.documents, selected],
    );
    const chatEligible = useMemo(
        () => selectedDocs.filter((doc) => doc.category === scope && doc.status === "ready" && doc.retrieval_enabled),
        [selectedDocs, scope],
    );
    const blockedDocuments = useMemo(
        () => selectedDocs.filter((doc) => !chatEligible.some((eligible) => eligible.id === doc.id)),
        [chatEligible, selectedDocs],
    );
    const readyDocuments = useMemo(
        () => catalog.documents.filter((doc) => doc.status === "ready" && doc.retrieval_enabled),
        [catalog.documents],
    );
    const categoryReadyDocuments = useMemo(
        () => readyDocuments.filter((doc) => doc.category === scope),
        [readyDocuments, scope],
    );
    const hasPendingDocuments = useMemo(
        () => catalog.documents.some((doc) => ["queued", "indexing"].includes(doc.status)),
        [catalog.documents],
    );
    const hasAnyReadyDocuments =
        readyDocuments.length > 0 ||
        (retrieval.status?.ready_document_count ?? 0) > 0;
    const hasCategoryReadyDocuments = categoryReadyDocuments.length > 0;

    const toggleSelected = (id: string) => {
        setEligibilityError(null);
        setSelected((items) => {
            if (items.includes(id)) return items.filter((item) => item !== id);
            if (items.length >= MAX_DOCUMENTS) {
                setActionError("最多只能選取 " + MAX_DOCUMENTS + " 份文件。");
                return items;
            }
            return [...items, id];
        });
    };

    const changeScope = (value: Category) => {
        setScope(value);
        setEligibilityError(null);
    };

    const openUpload = () => {
        setUploadCategory(scope);
        setUploadOpen(true);
    };

    const send = async () => {
        const question = draft.trim();
        if (!question || chatBusy || question.length > 20_000) return;
        if (!retrieval.status?.can_retrieve) {
            setEligibilityError(
                retrieval.error?.message ||
                    "知識庫正在準備中，完成後才能提問。",
            );
            return;
        }
        if (!hasCategoryReadyDocuments) {
            setEligibilityError("目前搜尋範圍尚無可用文件，請先上傳文件或切換分類。");
            return;
        }
        if (selectedDocs.length && blockedDocuments.length) {
            const names = blockedDocuments.map((doc) => doc.display_name).join("、");
            setEligibilityError("請先處理未符合目前搜尋條件的選取文件：" + names);
            return;
        }
        setEligibilityError(null);
        setChatError(null);
        setDraft("");
        setChat((items) => [...items, { role: "user", text: question }, { role: "assistant", text: "" }]);
        setChatBusy(true);
        try {
            await streamChat(
                { question, mode: scope, document_ids: selectedDocs.length ? chatEligible.map((doc) => doc.id) : [] },
                {
                    onDelta: (delta) => setChat((items) => {
                        const next = [...items];
                        const last = next[next.length - 1];
                        if (last?.role === "assistant") last.text += delta;
                        return next;
                    }),
                    onDone: (citations) => setChat((items) => {
                        const next = [...items];
                        const last = next[next.length - 1];
                        if (last?.role === "assistant") last.citations = citations;
                        return next;
                    }),
                },
            );
        } catch (error) {
            setChatError(errorText(error));
            setChat((items) => items.slice(0, -1));
        } finally {
            setChatBusy(false);
        }
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
        if (document.status !== "delete_failed" && !window.confirm("刪除「" + document.display_name + "」？此操作會移除來源與檢索資料。")) return;
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

    const startSlides = async () => {
        const eligible = selectedDocs.filter((doc) => doc.status === "ready" && supportsSlideGeneration(doc));
        if (!eligible.length || !slideTitle.trim()) return;
        try {
            await slideJob.start({
                title: slideTitle.trim(),
                document_ids: eligible.map((doc) => doc.id),
                slides_count: slideCount,
                guidance,
                tone,
            });
        } catch (error) {
            setActionError(errorText(error));
        }
    };

    return {
        catalog,
        slideJob,
        view,
        setView,
        collapsed,
        setCollapsed,
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
        qaError,
        loadModes,
        scope,
        changeScope,
        chat,
        draft,
        setDraft,
        send,
        chatBusy,
        chatError,
        eligibilityError,
        actionError,
        retrievalStatus: retrieval.status,
        retrievalLoading: retrieval.loading,
        retrievalError: retrieval.error?.message || null,
        reloadRetrieval: retrieval.reload,
        catalogLoading: catalog.loadingDocuments,
        hasPendingDocuments,
        hasAnyReadyDocuments,
        hasCategoryReadyDocuments,
        toggleSelected,
        mutateDocument,
        deleteDocument,
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
