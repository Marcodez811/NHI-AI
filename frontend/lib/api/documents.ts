/** Document, folder, ingestion, and QA-mode transport contracts. */
import { queryString, request, API_ROOT } from "./client";

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


