"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Download, Trash2 } from "lucide-react";
import { getApiErrorMessage } from "../../lib/api/client";
import { ARTIFACT_KIND_LABELS, artifactDownloadUrl, deleteArtifact, fetchArtifacts } from "../../lib/api/artifacts";
import type { Artifact } from "../../lib/api/artifacts";
import { deleteUserFile, fetchUserFiles, userFileContentUrl } from "../../lib/api/files";
import type { UserFile } from "../../lib/api/files";
import { formatBytes, formatDate } from "../workspace/WorkspaceViewUtils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "../ui/alert-dialog";
import { PromoteDialog } from "./PromoteDialog";

type Tab = "files" | "artifacts";
type PendingDelete = { kind: Tab; id: string; name: string } | null;

const KB_TYPES = new Set(["application/pdf", "text/plain", "text/markdown", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]);

function typeLabel(file: UserFile): string {
    if (file.kind === "image") return "圖片";
    if (file.mime_type === "application/pdf") return "PDF";
    if (file.mime_type.includes("wordprocessingml")) return "Word";
    if (file.mime_type === "text/markdown") return "Markdown";
    return "文字檔";
}

export function FilesPage() {
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const tab: Tab = searchParams.get("tab") === "artifacts" ? "artifacts" : "files";

    const [files, setFiles] = useState<UserFile[]>([]);
    const [artifacts, setArtifacts] = useState<Artifact[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [promoting, setPromoting] = useState<UserFile | null>(null);
    const [pendingDelete, setPendingDelete] = useState<PendingDelete>(null);

    const load = useCallback(async () => {
        setError(null);
        try {
            const [nextFiles, nextArtifacts] = await Promise.all([fetchUserFiles(), fetchArtifacts()]);
            setFiles(nextFiles);
            setArtifacts(nextArtifacts);
        } catch (caught) {
            setError(getApiErrorMessage(caught));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    const selectTab = (next: Tab) => {
        const params = new URLSearchParams(searchParams.toString());
        if (next === "artifacts") params.set("tab", "artifacts");
        else params.delete("tab");
        const query = params.toString();
        router.push(query ? `${pathname}?${query}` : pathname);
    };

    const confirmDelete = async () => {
        const target = pendingDelete;
        setPendingDelete(null);
        if (!target) return;
        try {
            if (target.kind === "files") await deleteUserFile(target.id);
            else await deleteArtifact(target.id);
            await load();
        } catch (caught) {
            setError(getApiErrorMessage(caught));
        }
    };

    const tabClass = (active: boolean) =>
        `-mb-px border-b-2 pb-2 text-sm font-medium transition-colors ${active ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"}`;

    return (
        <section className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6">
            <h1 className="mb-1 text-2xl font-semibold">我的檔案</h1>
            <p className="mb-6 text-sm text-muted-foreground">管理你上傳的檔案，以及 AI 為你產出的文件。</p>
            <div role="tablist" aria-label="我的檔案" className="mb-6 flex gap-6 border-b border-border">
                <button type="button" role="tab" aria-selected={tab === "files"} className={tabClass(tab === "files")} onClick={() => selectTab("files")}>
                    上傳的檔案
                </button>
                <button type="button" role="tab" aria-selected={tab === "artifacts"} className={tabClass(tab === "artifacts")} onClick={() => selectTab("artifacts")}>
                    產出的文件
                </button>
            </div>
            {error && <p role="alert" className="mb-4 text-sm text-destructive">{error}</p>}
            {loading ? (
                <p className="text-sm text-muted-foreground">載入中…</p>
            ) : tab === "files" ? (
                files.length === 0 ? (
                    <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                        還沒有上傳的檔案。在對話中按「＋」即可上傳。
                    </p>
                ) : (
                    <ul role="tabpanel" className="divide-y divide-border rounded-lg border border-border">
                        {files.map((file) => (
                            <li key={file.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2">
                                        <span className="truncate font-medium">{file.display_name}</span>
                                        {file.in_knowledge_base && <Badge variant="secondary" className="shrink-0">已加入知識庫</Badge>}
                                    </div>
                                    <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                                        <span>{typeLabel(file)}</span>
                                        <span>{formatBytes(file.size_bytes)}</span>
                                        <span>{formatDate(file.created_at)}</span>
                                        {file.origin_session_id && (
                                            <Link href={`/chat/${encodeURIComponent(file.origin_session_id)}`} className="underline underline-offset-2 hover:text-foreground">
                                                來源對話：{file.origin_session_title || "未命名對話"}
                                            </Link>
                                        )}
                                    </div>
                                </div>
                                <div className="flex shrink-0 items-center gap-1">
                                    <Button variant="ghost" size="sm" nativeButton={false} render={<a href={userFileContentUrl(file.id)} download />}>
                                        <Download /> 下載
                                    </Button>
                                    {!file.in_knowledge_base && file.kind !== "image" && KB_TYPES.has(file.mime_type) && (
                                        <Button type="button" variant="ghost" size="sm" onClick={() => setPromoting(file)}>加入知識庫</Button>
                                    )}
                                    <Button type="button" variant="ghost" size="sm" onClick={() => setPendingDelete({ kind: "files", id: file.id, name: file.display_name })}>
                                        <Trash2 /> 刪除
                                    </Button>
                                </div>
                            </li>
                        ))}
                    </ul>
                )
            ) : artifacts.length === 0 ? (
                <p className="rounded-lg border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
                    還沒有產出的文件。完成簡報後會出現在這裡。
                </p>
            ) : (
                <ul role="tabpanel" className="divide-y divide-border rounded-lg border border-border">
                    {artifacts.map((artifact) => (
                        <li key={artifact.id} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
                            <div className="min-w-0 flex-1">
                                <div className="truncate font-medium">{artifact.title}</div>
                                <div className="flex flex-wrap gap-x-3 text-xs text-muted-foreground">
                                    <span>{ARTIFACT_KIND_LABELS[artifact.kind] ?? "文件"}</span>
                                    <span>{formatBytes(artifact.size_bytes)}</span>
                                    <span>{formatDate(artifact.created_at)}</span>
                                    {artifact.source_workflow === "slides" && artifact.source_job_id && (
                                        <Link href={`/slides/${encodeURIComponent(artifact.source_job_id)}`} className="underline underline-offset-2 hover:text-foreground">
                                            查看簡報作業
                                        </Link>
                                    )}
                                </div>
                            </div>
                            <div className="flex shrink-0 items-center gap-1">
                                <Button variant="ghost" size="sm" nativeButton={false} render={<a href={artifactDownloadUrl(artifact.id)} download />}>
                                    <Download /> 下載
                                </Button>
                                <Button type="button" variant="ghost" size="sm" onClick={() => setPendingDelete({ kind: "artifacts", id: artifact.id, name: artifact.title })}>
                                    <Trash2 /> 刪除
                                </Button>
                            </div>
                        </li>
                    ))}
                </ul>
            )}

            <PromoteDialog file={promoting} onClose={() => setPromoting(null)} onDone={() => { setPromoting(null); void load(); }} />
            <AlertDialog open={pendingDelete !== null} onOpenChange={(open) => { if (!open) setPendingDelete(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>確定要刪除嗎？</AlertDialogTitle>
                        <AlertDialogDescription className="break-all">
                            「{pendingDelete?.name}」將被永久刪除，無法復原。
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>取消</AlertDialogCancel>
                        <AlertDialogAction variant="destructive" onClick={() => void confirmDelete()}>刪除</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </section>
    );
}
