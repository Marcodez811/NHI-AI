"use client";

import { useEffect, useMemo, useState } from "react";
import {
    ChevronDown,
    ChevronRight,
    Download,
    FileText,
    Folder,
    FolderPlus,
    MoreHorizontal,
    Pencil,
    RefreshCw,
    Search,
    Trash2,
    Upload,
} from "lucide-react";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "../ui/dropdown-menu";
import { Input } from "../ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import type {
    Category,
    DocumentRead,
    FolderRead,
    IngestionJobRead,
    QaModeInfo,
} from "../../lib/api/documents";
import {
    ActionButton,
    documentDisplayName,
    formatBytes,
    formatDate,
    statusLabel,
} from "./WorkspaceViewUtils";
import { CategorySelect, FolderSelect } from "./DocumentSelects";

const STATUS_LABELS: Record<string, string> = {
    all: "全部狀態",
    ready: "可使用",
    indexing: "建立索引中",
    queued: "等待處理",
    failed: "處理失敗",
};

function filterDocuments(
    docs: DocumentRead[],
    filters: {
        folderId: string | null;
        category: "all" | Category;
        status: string;
        query: string;
    },
): DocumentRead[] {
    const normalizedQuery = filters.query.trim().toLowerCase();
    return docs.filter((document) => {
        if (filters.folderId === "_uncategorized" && document.folder_id) return false;
        if (filters.folderId && filters.folderId !== "_uncategorized" && document.folder_id !== filters.folderId) return false;
        if (filters.category !== "all" && document.category !== filters.category) return false;
        if (filters.status !== "all" && document.status !== filters.status) return false;
        if (!normalizedQuery) return true;
        return documentDisplayName(document).toLowerCase().includes(normalizedQuery) ||
            document.original_filename.toLowerCase().includes(normalizedQuery);
    });
}

function IngestionDetails({
    document,
    getIngestion,
}: {
    document: DocumentRead;
    getIngestion: (id: string) => Promise<IngestionJobRead>;
}) {
    const [job, setJob] = useState<IngestionJobRead | null>(null);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        let active = true;
        const load = async () => {
            try {
                const next = await getIngestion(document.id);
                if (active) {
                    setJob(next);
                    setError(null);
                }
            } catch (requestError) {
                if (active)
                    setError(
                        requestError instanceof Error
                            ? requestError.message
                            : "索引狀態暫時無法取得。",
                    );
            }
        };
        void load();
        const timer = window.setInterval(() => void load(), 2500);
        return () => {
            active = false;
            window.clearInterval(timer);
        };
    }, [document.id, getIngestion]);
    return (
        <div className="mt-2 rounded-md bg-secondary/70 px-3 py-2 text-xs text-muted-foreground">
            {error ? (
                <span className="text-red-600">{error}</span>
            ) : job ? (
                <div className="grid gap-1 sm:grid-cols-3">
                    <span>階段：{job.stage || statusLabel(job.status)}</span>
                    <span>嘗試：{job.attempts}</span>
                    <span>
                        {job.error
                            ? `錯誤：${job.error}`
                            : `更新：${formatDate(job.updated_at)}`}
                    </span>
                </div>
            ) : (
                "載入索引詳情…"
            )}
        </div>
    );
}

function FolderManager({
    folders,
    onCreate,
    onRename,
    onDelete,
}: {
    folders: FolderRead[];
    onCreate: (name: string) => Promise<void>;
    onRename: (id: string, name: string) => Promise<void>;
    onDelete: (id: string) => Promise<void>;
}) {
    const [open, setOpen] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [folderEditor, setFolderEditor] = useState<{
        mode: "create" | "rename";
        id?: string;
        name: string;
    } | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<FolderRead | null>(null);
    const run = async (key: string, action: () => Promise<void>) => {
        setBusy(key);
        setError(null);
        try {
            await action();
            return true;
        } catch (actionError) {
            setError(
                actionError instanceof Error
                    ? actionError.message
                    : "資料夾操作失敗。",
            );
            return false;
        } finally {
            setBusy(null);
        }
    };
    const saveFolder = async () => {
        if (!folderEditor?.name.trim()) return;
        const editor = folderEditor;
        const success = await run(
            editor.mode === "create" ? "create" : editor.id || "rename",
            () =>
                editor.mode === "create"
                    ? onCreate(editor.name.trim())
                    : onRename(editor.id || "", editor.name.trim()),
        );
        if (success) setFolderEditor(null);
    };
    const confirmDelete = async () => {
        if (!deleteTarget) return;
        const target = deleteTarget;
        const success = await run(target.id, () => onDelete(target.id));
        if (success) setDeleteTarget(null);
    };
    return (
        <div className="relative">
            <Button
                type="button"
                aria-expanded={open}
                variant="outline"
                className="h-8 justify-between px-3 text-xs font-medium"
                onClick={() => setOpen((value) => !value)}
            >
                <span className="flex items-center gap-2">
                    <Folder size={16} className="text-primary" />
                    資料夾管理
                </span>
                <ChevronDown size={15} className={open ? "rotate-180" : ""} />
            </Button>
            {open && (
                    <div className="absolute right-0 top-10 z-10 w-64 space-y-2 rounded-lg border border-border bg-card p-3 shadow-lg">
                    <Button
                        type="button"
                        onClick={() => setFolderEditor({ mode: "create", name: "" })}
                        disabled={busy !== null}
                        variant="outline"
                        className="h-auto w-full justify-start border-dashed px-3 py-2 text-left text-xs text-primary"
                    >
                        <FolderPlus size={14} />
                        新增資料夾
                    </Button>
                    {folders.map((item) => (
                        <div
                            key={item.id}
                            className="flex items-center justify-between rounded-md bg-secondary/60 px-3 py-2 text-xs"
                        >
                            <span className="truncate">{item.name}</span>
                            <span className="flex shrink-0">
                                <ActionButton
                                    label={`重新命名 ${item.name}`}
                                    disabled={busy !== null}
                                    onClick={() =>
                                        setFolderEditor({
                                            mode: "rename",
                                            id: item.id,
                                            name: item.name,
                                        })
                                    }
                                >
                                    <Pencil size={13} />
                                </ActionButton>
                                <ActionButton
                                    label={`刪除 ${item.name}`}
                                    disabled={busy !== null}
                                    destructive
                                    onClick={() => setDeleteTarget(item)}
                                >
                                    <Trash2 size={13} />
                                </ActionButton>
                            </span>
                        </div>
                    ))}
                    {error && <p className="text-xs text-red-600">{error}</p>}
                </div>
            )}
            <Dialog
                open={Boolean(folderEditor)}
                onOpenChange={(nextOpen) => !nextOpen && setFolderEditor(null)}
            >
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>
                            {folderEditor?.mode === "rename"
                                ? "重新命名資料夾"
                                : "新增資料夾"}
                        </DialogTitle>
                        <DialogDescription>
                            用資料夾整理來源文件，方便在知識庫和工作流中快速篩選。
                        </DialogDescription>
                    </DialogHeader>
                    <label className="grid gap-2 text-sm font-medium" htmlFor="folder-name">
                        資料夾名稱
                        <Input
                            id="folder-name"
                            value={folderEditor?.name || ""}
                            onChange={(event) =>
                                setFolderEditor((current) =>
                                    current
                                        ? { ...current, name: event.target.value }
                                        : current,
                                )
                            }
                            autoFocus
                            maxLength={80}
                        />
                    </label>
                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={() => setFolderEditor(null)}>
                            取消
                        </Button>
                        <Button type="button" disabled={!folderEditor?.name.trim() || busy !== null} onClick={() => void saveFolder()}>
                            儲存
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
            <Dialog
                open={Boolean(deleteTarget)}
                onOpenChange={(nextOpen) => !nextOpen && setDeleteTarget(null)}
            >
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>刪除資料夾？</DialogTitle>
                        <DialogDescription>
                            刪除「{deleteTarget?.name}」後，文件仍會保留，但會移至未分類。
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={() => setDeleteTarget(null)}>
                            取消
                        </Button>
                        <Button type="button" variant="destructive" disabled={busy !== null} onClick={() => void confirmDelete()}>
                            刪除資料夾
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

export function FilesView({
    docs,
    folders,
    folderId,
    setFolderId,
    folderName,
    query,
    setQuery,
    onUpload,
    onUpdate,
    onDelete,
    onDownload,
    onCreateFolder,
    onRenameFolder,
    onDeleteFolder,
    loading,
    error,
    actionError,
    getIngestion,
    modes,
}: {
    docs: DocumentRead[];
    folders: FolderRead[];
    folderId: string | null;
    setFolderId: (value: string | null) => void;
    folderName: string;
    query: string;
    setQuery: (value: string) => void;
    onUpload: () => void;
    onUpdate: (
        id: string,
        changes: {
            display_name?: string;
            category?: Category;
            folder_id?: string | null;
            retrieval_enabled?: boolean;
        },
    ) => Promise<void>;
    onDelete: (document: DocumentRead) => void;
    onDownload: (document: DocumentRead) => void;
    onCreateFolder: (name: string) => Promise<void>;
    onRenameFolder: (id: string, name: string) => Promise<void>;
    onDeleteFolder: (id: string) => Promise<void>;
    loading: boolean;
    error: string | null;
    actionError: string | null;
    getIngestion: (id: string) => Promise<IngestionJobRead>;
    modes: QaModeInfo[];
}) {
    const [expanded, setExpanded] = useState<string | null>(null);
    const [detailsDoc, setDetailsDoc] = useState<DocumentRead | null>(null);
    const [detailsName, setDetailsName] = useState("");
    const [detailsCategory, setDetailsCategory] = useState<Category>("bei_can");
    const [detailsFolder, setDetailsFolder] = useState<string>("_uncategorized");
    const [detailsRetrieval, setDetailsRetrieval] = useState(true);
    const [categoryFilter, setCategoryFilter] = useState<"all" | Category>("all");
    const [statusFilter, setStatusFilter] = useState("all");
    const visible = useMemo(
        () => filterDocuments(docs, {
            folderId,
            category: categoryFilter,
            status: statusFilter,
            query,
        }),
        [docs, folderId, query, categoryFilter, statusFilter],
    );
    const openDetails = (doc: DocumentRead) => {
        setDetailsDoc(doc);
        setDetailsName(doc.display_name);
        setDetailsCategory(doc.category);
        setDetailsFolder(doc.folder_id || "_uncategorized");
        setDetailsRetrieval(doc.retrieval_enabled);
    };
    const closeDetails = () => setDetailsDoc(null);
    const saveDetails = async () => {
        if (!detailsDoc || !detailsName.trim()) return;
        await onUpdate(detailsDoc.id, {
            display_name: detailsName.trim(),
            category: detailsCategory,
            folder_id: detailsFolder === "_uncategorized" ? null : detailsFolder,
            retrieval_enabled: detailsRetrieval,
        });
        closeDetails();
    };
    return (
        <section className="px-5 py-8 lg:px-10 lg:py-10">
            <div className="mx-auto max-w-[1100px]">
            <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
                <div>
                    <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                        知識庫 <ChevronRight size={13} /> {folderName}
                    </div>
                    <h1 className="text-[24px] font-semibold tracking-tight">
                        知識庫管理
                    </h1>
                    <p className="mt-1 text-sm text-muted-foreground">
                        管理文件、分類資料，並供生成工作流程使用。
                    </p>
                </div>
            <Button
                type="button"
                onClick={onUpload}
                className="gap-2"
            >
                <Upload size={16} />
                上傳文件
            </Button>
            </div>
            {(error || actionError) && (
                <div className="mb-4 flex items-center justify-between rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    <span>{error || actionError}</span>
                </div>
            )}
            <div>
                <div>
                    <div className="mb-4 flex flex-wrap items-center gap-2">
                        <div className="flex min-w-56 flex-1 items-center gap-2 rounded-md border border-border bg-card px-3">
                            <Search size={16} />
                            <Input
                                value={query}
                                onChange={(event) =>
                                    setQuery(event.target.value)
                                }
                                placeholder="搜尋文件名稱…"
                                className="h-8 border-0 bg-transparent py-2 text-sm shadow-none focus-visible:ring-0"
                            />
                        </div>
                        <FolderSelect
                            folders={folders}
                            value={folderId || "_all"}
                            onValueChange={(value) => setFolderId(value === "_all" ? null : value)}
                            includeAll
                            size="sm"
                            className="w-36"
                            aria-label="資料夾篩選"
                        />
                        <CategorySelect
                            modes={modes}
                            value={categoryFilter}
                            onValueChange={setCategoryFilter}
                            includeAll
                            size="sm"
                            className="w-32"
                            aria-label="分類篩選"
                        />
                        <Select items={STATUS_LABELS} value={statusFilter} onValueChange={(value) => setStatusFilter(value || "all")}>
                            <SelectTrigger size="sm" className="w-32" aria-label="狀態篩選"><SelectValue>{(value) => STATUS_LABELS[value as string] ?? "全部狀態"}</SelectValue></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">全部狀態</SelectItem>
                                <SelectItem value="ready">可使用</SelectItem>
                                <SelectItem value="indexing">建立索引中</SelectItem>
                                <SelectItem value="queued">等待處理</SelectItem>
                                <SelectItem value="failed">處理失敗</SelectItem>
                            </SelectContent>
                        </Select>
                        <FolderManager folders={folders} onCreate={onCreateFolder} onRename={onRenameFolder} onDelete={onDeleteFolder} />
                    </div>
                    <div className="overflow-hidden rounded-xl border border-border bg-card">
                        <div className="border-b border-border px-4 py-3 text-xs text-muted-foreground">
                            {loading ? "載入中…" : `${visible.length} 個項目`} ·{" "}
                            {folderName}
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full min-w-[820px] text-left text-sm">
                                <thead className="border-b border-border bg-muted/40 text-xs text-muted-foreground">
                                    <tr>
                                        <th className="px-3 py-3">名稱</th>
                                        <th className="px-3 py-3">分類</th>
                                        <th className="px-3 py-3">資料夾</th>
                                        <th className="px-3 py-3">修改時間</th>
                                        <th className="px-3 py-3">狀態</th>
                                        <th className="px-3 py-3">操作</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {visible.map((doc) => {
                                        const deleting =
                                            doc.status === "deleting";
                                        const expandedRow = expanded === doc.id;
                                        return (
                                            <tr
                                                key={doc.id}
                                                className="border-b border-border last:border-0 hover:bg-muted/30"
                                            >
                                                <td className="px-3 py-3 align-top">
                                                    <div className="flex items-start gap-3">
                                                        <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-secondary">
                                                            <FileText
                                                                size={16}
                                                            />
                                                        </div>
                                                        <div className="min-w-0">
                                                            <div className="truncate font-medium">
                                                                {documentDisplayName(
                                                                    doc,
                                                                )}
                                                            </div>
                                                            <div className="truncate text-xs text-muted-foreground">
                                                                {
                                                                    doc.original_filename
                                                                }
                                                            </div>
                                                            {(doc.status ===
                                                                "failed" ||
                                                                doc.status ===
                                                                    "delete_failed") &&
                                                                doc.error && (
                                                                    <div className="mt-1 max-w-xs text-xs text-red-600">
                                                                        {
                                                                            doc.error
                                                                        }
                                                                    </div>
                                                                )}
                                                            {expandedRow && (
                                                                <IngestionDetails
                                                                    document={
                                                                        doc
                                                                    }
                                                                    getIngestion={
                                                                        getIngestion
                                                                    }
                                                                />
                                                            )}
                                                        </div>
                                                    </div>
                                                </td>
                                                <td className="px-3 py-3 align-top text-xs text-muted-foreground">
                                                    {modes.find((mode) => mode.mode === doc.category)?.label || doc.category}
                                                </td>
                                                <td className="px-3 py-3 align-top text-xs text-muted-foreground">
                                                    {doc.folder_id ? folders.find((folder) => folder.id === doc.folder_id)?.name || "資料夾" : "未分類"}
                                                </td>
                                                <td className="px-3 py-3 align-top text-xs text-muted-foreground">
                                                    {formatDate(
                                                        doc.updated_at ||
                                                            doc.created_at,
                                                    )}
                                                </td>
                                                <td className="px-3 py-3 align-top">
                                                    <Button
                                                        type="button"
                                                        onClick={() =>
                                                            setExpanded(
                                                                expandedRow
                                                                    ? null
                                                                    : doc.id,
                                                            )
                                                        }
                                                        disabled={deleting}
                                                        variant="ghost"
                                                        size="sm"
                                                        className="h-auto gap-1.5 p-0 text-xs"
                                                    >
                                                        <span
                                                            className={`size-1.5 rounded-full ${doc.status === "ready" ? "bg-emerald-500" : doc.status === "indexing" ? "bg-info" : doc.status === "failed" || doc.status === "delete_failed" ? "bg-red-500" : doc.status === "deleting" ? "bg-slate-400" : "bg-amber-500"}`}
                                                        />
                                                        {statusLabel(
                                                            doc.status,
                                                        )}
                                                        {[
                                                            "queued",
                                                            "indexing",
                                                            "failed",
                                                        ].includes(
                                                            doc.status,
                                                        ) && (
                                                            <ChevronDown
                                                                size={13}
                                                                className={
                                                                    expandedRow
                                                                        ? "rotate-180"
                                                                        : ""
                                                                }
                                                            />
                                                        )}
                                                    </Button>
                                                </td>
                                                <td className="px-3 py-3 align-top">
                                                    <DropdownMenu>
                                                        <DropdownMenuTrigger render={<Button type="button" variant="ghost" size="icon-sm" aria-label={`更多操作：${documentDisplayName(doc)}`} />}>
                                                            <MoreHorizontal size={15} />
                                                        </DropdownMenuTrigger>
                                                        <DropdownMenuContent align="end" className="w-40">
                                                            <DropdownMenuItem onClick={() => openDetails(doc)}><Pencil size={14} />編輯文件</DropdownMenuItem>
                                                            <DropdownMenuItem onClick={() => setExpanded(expandedRow ? null : doc.id)}><FileText size={14} />索引詳情</DropdownMenuItem>
                                                            <DropdownMenuItem disabled={deleting} onClick={() => onDownload(doc)}><Download size={14} />下載文件</DropdownMenuItem>
                                                            <DropdownMenuSeparator />
                                                            <DropdownMenuItem variant="destructive" disabled={deleting} onClick={() => onDelete(doc)}>{doc.status === "delete_failed" ? <RefreshCw size={14} /> : <Trash2 size={14} />}{doc.status === "delete_failed" ? "重試刪除" : "刪除文件"}</DropdownMenuItem>
                                                        </DropdownMenuContent>
                                                    </DropdownMenu>
                                                    {doc.status ===
                                                        "deleting" && (
                                                        <div className="mt-1 text-xs text-muted-foreground">
                                                            刪除工作進行中…
                                                        </div>
                                                    )}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                        {!loading && !visible.length && (
                            <div className="p-14 text-center text-sm text-muted-foreground">
                                找不到符合條件的文件
                            </div>
                        )}
                    </div>
                </div>
            </div>
            <Dialog open={Boolean(detailsDoc)} onOpenChange={(open) => !open && closeDetails()}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle>編輯文件</DialogTitle>
                        <DialogDescription>更新文件的顯示名稱、分類、資料夾與檢索設定。</DialogDescription>
                    </DialogHeader>
                    <div className="grid gap-4">
                        <label className="grid gap-2 text-sm font-medium" htmlFor="document-display-name">
                            文件名稱
                            <Input id="document-display-name" value={detailsName} onChange={(event) => setDetailsName(event.target.value)} />
                        </label>
                        <div className="grid gap-2 text-sm font-medium">
                            分類
                            <CategorySelect
                                modes={modes}
                                value={detailsCategory}
                                onValueChange={(value) => setDetailsCategory(value as Category)}
                                aria-label="文件分類"
                            />
                        </div>
                        <div className="grid gap-2 text-sm font-medium">
                            資料夾
                            <FolderSelect
                                folders={folders}
                                value={detailsFolder}
                                onValueChange={setDetailsFolder}
                                aria-label="文件資料夾"
                            />
                        </div>
                        <label className="flex items-center gap-2 text-sm">
                            <input type="checkbox" checked={detailsRetrieval} onChange={(event) => setDetailsRetrieval(event.target.checked)} />
                            啟用檢索，允許對話引用此文件
                        </label>
                        {detailsDoc && <p className="text-xs text-muted-foreground">原始檔案：{detailsDoc.original_filename} · {formatBytes(detailsDoc.size_bytes)}</p>}
                    </div>
                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={closeDetails}>取消</Button>
                        <Button type="button" disabled={!detailsName.trim()} onClick={() => void saveDetails()}>儲存變更</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
            </div>
        </section>
    );
}
