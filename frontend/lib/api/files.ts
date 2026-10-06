/** "My files" (user-owned uploads) transport contracts. Field names follow docs/9_29_files_and_artifacts_spec.md. */
import { API_ROOT, request } from "./client";
import type { Category } from "./documents";

export type UserFileKind = "document" | "image";

export interface UserFile {
    id: string;
    display_name: string;
    mime_type: string;
    kind: UserFileKind;
    size_bytes: number;
    status: string;
    created_at: string;
    origin_session_id: string | null;
    origin_session_title: string | null;
    in_knowledge_base: boolean;
}

export interface PromoteFilePayload {
    category: Category;
    folder_id?: string | null;
}

export async function fetchUserFiles(): Promise<UserFile[]> {
    const body = await request<UserFile[] | { items: UserFile[] }>("/files");
    return Array.isArray(body) ? body : body?.items ?? [];
}

export function userFileContentUrl(id: string): string {
    return `${API_ROOT}/files/${encodeURIComponent(id)}/content`;
}

export async function deleteUserFile(id: string): Promise<void> {
    await request<void>(`/files/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** Copies the file into the knowledge base through the normal ingestion pipeline. */
export async function promoteUserFile(id: string, payload: PromoteFilePayload): Promise<void> {
    await request<unknown>(`/files/${encodeURIComponent(id)}/promote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category: payload.category, folder_id: payload.folder_id ?? null }),
    });
}
