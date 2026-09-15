"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    createFolder as createFolderRequest,
    deleteDocument as deleteDocumentRequest,
    deleteFolder as deleteFolderRequest,
    DocumentListFilters,
    DocumentRead,
    DocumentUpdate,
    DocumentUploadResponse,
    fetchDocuments,
    fetchFolders,
    FolderRead,
    getDocument,
    getIngestionStatus,
    IngestionJobRead,
    renameFolder as renameFolderRequest,
    updateDocument as updateDocumentRequest,
    uploadDocument as uploadDocumentRequest,
    Category,
} from "../api/documents";
import { ApiError } from "../api/client";
import { asApiError } from "../api-error";

export interface UseDocumentsOptions {
    autoLoad?: boolean;
    filters?: DocumentListFilters;
}

export interface UseDocumentsResult {
    documents: DocumentRead[];
    folders: FolderRead[];
    loading: boolean;
    loadingDocuments: boolean;
    loadingFolders: boolean;
    documentsError: ApiError | null;
    foldersError: ApiError | null;
    error: ApiError | null;
    reload: () => Promise<void>;
    reloadDocuments: () => Promise<DocumentRead[]>;
    reloadFolders: () => Promise<FolderRead[]>;
    refreshDocument: (id: string) => Promise<DocumentRead>;
    upload: (
        file: File,
        category: Category,
        folderId?: string | null,
    ) => Promise<DocumentUploadResponse>;
    updateDocument: (
        id: string,
        update: DocumentUpdate,
    ) => Promise<DocumentRead>;
    deleteDocument: (id: string) => Promise<DocumentRead | undefined>;
    getIngestion: (id: string) => Promise<IngestionJobRead>;
    createFolder: (name: string) => Promise<FolderRead>;
    renameFolder: (id: string, name: string) => Promise<FolderRead>;
    deleteFolder: (id: string) => Promise<void>;
}

/** Replace one document while preserving the server's returned state. */
function replaceDocument(
    documents: DocumentRead[],
    next: DocumentRead,
): DocumentRead[] {
    const index = documents.findIndex((document) => document.id === next.id);
    if (index < 0) return [...documents, next];
    const updated = documents.slice();
    updated[index] = next;
    return updated;
}

/**
 * Owns the document/folder catalog lifecycle used by the workspace views.
 * Documents and folders load independently, so a folder endpoint failure does
 * not prevent the document list from being usable (or vice versa).
 */
export function useDocuments(options: UseDocumentsOptions = {}): UseDocumentsResult {
    const mounted = useRef(true);
    // Keep the callback/effect stable when a caller constructs an equivalent
    // filter object inline on each render.
    const filterKey = JSON.stringify(options.filters ?? {});
    const filters = useMemo(() => options.filters, [filterKey]);
    const autoLoad = options.autoLoad ?? true;

    const [documents, setDocuments] = useState<DocumentRead[]>([]);
    const [folders, setFolders] = useState<FolderRead[]>([]);
    const [loadingDocuments, setLoadingDocuments] = useState(autoLoad);
    const [loadingFolders, setLoadingFolders] = useState(autoLoad);
    const [documentsError, setDocumentsError] = useState<ApiError | null>(null);
    const [foldersError, setFoldersError] = useState<ApiError | null>(null);

    useEffect(() => {
        mounted.current = true;
        return () => {
            mounted.current = false;
        };
    }, []);

    const reloadDocuments = useCallback(async (): Promise<DocumentRead[]> => {
        setLoadingDocuments(true);
        setDocumentsError(null);
        try {
            const next = await fetchDocuments(filters);
            if (mounted.current) setDocuments(next);
            return next;
        } catch (error) {
            const apiError = asApiError(error, "文件清單暫時無法取得。");
            if (mounted.current) setDocumentsError(apiError);
            throw apiError;
        } finally {
            if (mounted.current) setLoadingDocuments(false);
        }
    }, [filters]);

    const reloadFolders = useCallback(async (): Promise<FolderRead[]> => {
        setLoadingFolders(true);
        setFoldersError(null);
        try {
            const next = await fetchFolders();
            if (mounted.current) setFolders(next);
            return next;
        } catch (error) {
            const apiError = asApiError(error, "資料夾清單暫時無法取得。");
            if (mounted.current) setFoldersError(apiError);
            throw apiError;
        } finally {
            if (mounted.current) setLoadingFolders(false);
        }
    }, []);

    const reload = useCallback(async (): Promise<void> => {
        // allSettled keeps the two independent results available when only one
        // endpoint is unhealthy; each loader owns its own error state.
        await Promise.allSettled([reloadDocuments(), reloadFolders()]);
    }, [reloadDocuments, reloadFolders]);

    useEffect(() => {
        if (autoLoad) void reload();
    }, [autoLoad, reload]);

    const refreshDocument = useCallback(async (id: string): Promise<DocumentRead> => {
        const next = await getDocument(id);
        if (mounted.current) setDocuments((current) => replaceDocument(current, next));
        return next;
    }, []);

    const upload = useCallback(async (
        file: File,
        category: Category,
        folderId?: string | null,
    ): Promise<DocumentUploadResponse> => {
        const next = await uploadDocumentRequest(file, category, folderId);
        if (mounted.current) setDocuments((current) => replaceDocument(current, next));
        return next;
    }, []);

    const updateDocument = useCallback(async (
        id: string,
        update: DocumentUpdate,
    ): Promise<DocumentRead> => {
        const next = await updateDocumentRequest(id, update);
        if (mounted.current) setDocuments((current) => replaceDocument(current, next));
        return next;
    }, []);

    const deleteDocument = useCallback(async (
        id: string,
    ): Promise<DocumentRead | undefined> => {
        // The target API returns the document in its `deleting` state. The
        // undefined branch keeps the hook compatible with an older 204 server
        // during rollout and removes the row only after that older request
        // succeeds.
        const next = await deleteDocumentRequest(id);
        if (mounted.current) {
            setDocuments((current) => {
                if (next) return replaceDocument(current, next);
                return current.filter((document) => document.id !== id);
            });
        }
        return next;
    }, []);

    const getIngestion = useCallback(
        (id: string): Promise<IngestionJobRead> => getIngestionStatus(id),
        [],
    );

    const createFolder = useCallback(async (name: string): Promise<FolderRead> => {
        const next = await createFolderRequest(name);
        if (mounted.current) setFolders((current) => [...current, next]);
        return next;
    }, []);

    const renameFolder = useCallback(async (
        id: string,
        name: string,
    ): Promise<FolderRead> => {
        const next = await renameFolderRequest(id, name);
        if (mounted.current) {
            setFolders((current) => {
                const index = current.findIndex((folder) => folder.id === id);
                if (index < 0) return [...current, next];
                const updated = current.slice();
                updated[index] = next;
                return updated;
            });
        }
        return next;
    }, []);

    const deleteFolder = useCallback(async (id: string): Promise<void> => {
        await deleteFolderRequest(id);
        if (mounted.current) {
            setFolders((current) => current.filter((folder) => folder.id !== id));
        }
    }, []);

    const error = useMemo(
        () => documentsError ?? foldersError,
        [documentsError, foldersError],
    );

    return {
        documents,
        folders,
        loading: loadingDocuments || loadingFolders,
        loadingDocuments,
        loadingFolders,
        documentsError,
        foldersError,
        error,
        reload,
        reloadDocuments,
        reloadFolders,
        refreshDocument,
        upload,
        updateDocument,
        deleteDocument,
        getIngestion,
        createFolder,
        renameFolder,
        deleteFolder,
    };
}
