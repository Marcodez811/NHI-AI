/** Generated outputs (decks, drafts, reports). Field names follow docs/9_29_files_and_artifacts_spec.md. */
import { API_ROOT, request } from "./client";

export type ArtifactKind = "slide_deck" | "news_draft" | "report";
export type ArtifactWorkflow = "slides" | "news" | "chat";

export const ARTIFACT_KIND_LABELS: Record<ArtifactKind, string> = {
    slide_deck: "簡報",
    news_draft: "新聞稿",
    report: "報告",
};

export interface Artifact {
    id: string;
    kind: ArtifactKind;
    title: string;
    mime_type: string;
    size_bytes: number;
    source_workflow: ArtifactWorkflow;
    source_job_id: string | null;
    created_at: string;
}

export async function fetchArtifacts(): Promise<Artifact[]> {
    const body = await request<Artifact[] | { items: Artifact[] }>("/artifacts");
    return Array.isArray(body) ? body : body?.items ?? [];
}

export function artifactDownloadUrl(id: string): string {
    return `${API_ROOT}/artifacts/${encodeURIComponent(id)}/download`;
}

export async function deleteArtifact(id: string): Promise<void> {
    await request<void>(`/artifacts/${encodeURIComponent(id)}`, { method: "DELETE" });
}
