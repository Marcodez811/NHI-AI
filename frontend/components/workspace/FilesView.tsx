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
import { Input } from "../ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import type {
    Category,
    DocumentRead,
    FolderRead,
    IngestionJobRead,
    QaModeInfo,
} from "../../lib/api/documents";
import { MAX_DOCUMENTS } from "../../lib/api/documents";
import {
    ActionButton,
    documentDisplayName,
    formatBytes,
    formatDate,
    statusLabel,
} from "./WorkspaceViewUtils";

export function IngestionDetails({
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

export function FolderManager({
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
    const run = async (key: string, action: () => Promise<void>) => {
        setBusy(key);
        setError(null);
        try {
            await action();
        } catch (actionError) {
            setError(
                actionError instanceof Error
                    ? actionError.message
                    : "資料夾操作失敗。",
            );
        } finally {
            setBusy(null);
        }
    };
    const create = () => {
        const name = window.prompt("新資料夾名稱");
        if (name?.trim()) void run("create", () => onCreate(name.trim()));
    };
    return (
        <div className="rounded-xl border border-border bg-card p-4">
            <Button
                type="button"
                aria-expanded={open}
                variant="ghost"
                className="h-auto w-full justify-between px-0 text-sm font-semibold"
                onClick={() => setOpen((value) => !value)}
            >
                <span className="flex items-center gap-2">
                    <Folder size={16} className="text-primary" />
                    資料夾管理
                </span>
                <ChevronDown size={15} className={open ? "rotate-180" : ""} />
            </Button>
            {open && (
                <div className="mt-3 space-y-2">
                    <Button
                        type="button"
                        onClick={create}
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
                                    onClick={() => {
                                        const name = window.prompt(
                                            "資料夾名稱",
                                            item.name,
                                        );
                                        if (name?.trim())
                                            void run(item.id, () =>
                                                onRename(item.id, name.trim()),
                                            );
                                    }}
                                >
                                    <Pencil size={13} />
                                </ActionButton>
                                <ActionButton
                                    label={`刪除 ${item.name}`}
                                    disabled={busy !== null}
                                    destructive
                                    onClick={() => {
                                        if (
                                            window.confirm(
                                                `刪除資料夾「${item.name}」？`,
                                            )
                                        )
                                            void run(item.id, () =>
                                                onDelete(item.id),
                                            );
                                    }}
                                >
                                    <Trash2 size={13} />
                                </ActionButton>
                            </span>
                        </div>
                    ))}
                    {error && <p className="text-xs text-red-600">{error}</p>}
                </div>
            )}
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
    selected,
    onToggle,
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
    selected: string[];
    onToggle: (id: string) => void;
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
    const visible = useMemo(
        () =>
            docs.filter(
                (doc) =>
                    (!folderId || doc.folder_id === folderId) &&
                    (!query ||
                        documentDisplayName(doc)
                            .toLowerCase()
                            .includes(query.toLowerCase()) ||
                        doc.original_filename
                            .toLowerCase()
                            .includes(query.toLowerCase())),
            ),
        [docs, folderId, query],
    );
    return (
        <section className="p-5 lg:p-8">
            <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
                <div>
                    <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                        知識庫 <ChevronRight size={13} /> {folderName}
                    </div>
                    <h1 className="text-2xl font-semibold tracking-tight">
                        所有文件
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
            <div className="grid gap-5 lg:grid-cols-[14rem_1fr]">
                <div className="space-y-3">
                    <div className="rounded-xl border border-border bg-card p-3">
                        <div className="mb-2 text-xs font-semibold text-muted-foreground">
                            文件夾
                        </div>
                        <Button
                            type="button"
                            variant="ghost"
                            onClick={() => setFolderId(null)}
                            className={`mb-1 h-auto w-full justify-between px-3 py-2 text-left text-sm ${!folderId ? "bg-primary/10 text-primary" : ""}`}
                        >
                            <span>全部文件</span>
                            <span>{docs.length}</span>
                        </Button>
                        {folders.map((item) => (
                            <Button
                                type="button"
                                variant="ghost"
                                key={item.id}
                                onClick={() => setFolderId(item.id)}
                                className={`mb-1 h-auto w-full justify-between px-3 py-2 text-left text-sm ${folderId === item.id ? "bg-primary/10 text-primary" : ""}`}
                            >
                                <span className="truncate">{item.name}</span>
                                <span>
                                    {
                                        docs.filter(
                                            (doc) => doc.folder_id === item.id,
                                        ).length
                                    }
                                </span>
                            </Button>
                        ))}
                    </div>
                    <FolderManager
                        folders={folders}
                        onCreate={onCreateFolder}
                        onRename={onRenameFolder}
                        onDelete={onDeleteFolder}
                    />
                </div>
                <div>
                    <div className="mb-5 flex flex-wrap gap-2">
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
                        <span className="flex items-center rounded-md bg-secondary px-3 text-xs text-muted-foreground">
                            已選取 {selected.length} / {MAX_DOCUMENTS}
                        </span>
                    </div>
                    <div className="overflow-hidden rounded-xl border border-border bg-card">
                        <div className="border-b border-border px-4 py-3 text-xs text-muted-foreground">
                            {loading ? "載入中…" : `${visible.length} 個項目`} ·{" "}
                            {folderName}
                        </div>
                        <div className="overflow-x-auto">
                            <table className="w-full min-w-[900px] text-left text-sm">
                                <thead className="border-b border-border bg-muted/40 text-xs text-muted-foreground">
                                    <tr>
                                        <th className="w-10 px-4 py-3" />
                                        <th className="px-3 py-3">名稱</th>
                                        <th className="px-3 py-3">分類</th>
                                        <th className="px-3 py-3">資料夾</th>
                                        <th className="px-3 py-3">修改時間</th>
                                        <th className="px-3 py-3">大小</th>
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
                                                <td className="px-4 py-3 align-top">
                                                    <input
                                                        type="checkbox"
                                                        checked={selected.includes(
                                                            doc.id,
                                                        )}
                                                        disabled={deleting}
                                                        onChange={() =>
                                                            onToggle(doc.id)
                                                        }
                                                        aria-label={`選取 ${documentDisplayName(doc)}`}
                                                    />
                                                </td>
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
                                                <td className="px-3 py-3 align-top">
                                                    <Select
                                                        value={doc.category}
                                                        disabled={deleting}
                                                        onValueChange={(value) =>
                                                            void onUpdate(doc.id, { category: value as Category })
                                                        }
                                                    >
                                                        <SelectTrigger size="sm" className="max-w-32" aria-label={`分類 ${documentDisplayName(doc)}`}>
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {modes.map((mode) => <SelectItem value={mode.mode} key={mode.mode}>{mode.label}</SelectItem>)}
                                                        </SelectContent>
                                                    </Select>
                                                </td>
                                                <td className="px-3 py-3 align-top">
                                                    <Select
                                                        value={doc.folder_id || "_uncategorized"}
                                                        disabled={deleting}
                                                        onValueChange={(value) =>
                                                            void onUpdate(doc.id, { folder_id: value === "_uncategorized" ? null : value })
                                                        }
                                                    >
                                                        <SelectTrigger size="sm" className="max-w-32" aria-label={`資料夾 ${documentDisplayName(doc)}`}>
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            <SelectItem value="_uncategorized">未分類</SelectItem>
                                                            {folders.map((item) => <SelectItem value={item.id} key={item.id}>{item.name}</SelectItem>)}
                                                        </SelectContent>
                                                    </Select>
                                                </td>
                                                <td className="px-3 py-3 align-top text-xs text-muted-foreground">
                                                    {formatDate(
                                                        doc.updated_at ||
                                                            doc.created_at,
                                                    )}
                                                </td>
                                                <td className="px-3 py-3 align-top text-xs text-muted-foreground">
                                                    {formatBytes(
                                                        doc.size_bytes,
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
                                                            className={`size-1.5 rounded-full ${doc.status === "ready" ? "bg-emerald-500" : doc.status === "failed" || doc.status === "delete_failed" ? "bg-red-500" : doc.status === "deleting" ? "bg-slate-400" : "bg-amber-500"}`}
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
                                                    <div className="flex items-center gap-0">
                                                        <ActionButton
                                                            label="重新命名"
                                                            disabled={deleting}
                                                            onClick={() => {
                                                                const name =
                                                                    window.prompt(
                                                                        "文件名稱",
                                                                        doc.display_name,
                                                                    );
                                                                if (
                                                                    name?.trim()
                                                                )
                                                                    void onUpdate(
                                                                        doc.id,
                                                                        {
                                                                            display_name:
                                                                                name.trim(),
                                                                        },
                                                                    );
                                                            }}
                                                        >
                                                            <Pencil size={14} />
                                                        </ActionButton>
                                                        <ActionButton
                                                            label={
                                                                doc.retrieval_enabled
                                                                    ? "停用檢索"
                                                                    : "啟用檢索"
                                                            }
                                                            disabled={deleting}
                                                            onClick={() =>
                                                                void onUpdate(
                                                                    doc.id,
                                                                    {
                                                                        retrieval_enabled:
                                                                            !doc.retrieval_enabled,
                                                                    },
                                                                )
                                                            }
                                                        >
                                                            <span
                                                                className={`block size-3 rounded-full border-2 ${doc.retrieval_enabled ? "border-emerald-500 bg-emerald-500" : "border-muted-foreground"}`}
                                                            />
                                                        </ActionButton>
                                                        <ActionButton
                                                            label="下載"
                                                            disabled={deleting}
                                                            onClick={() =>
                                                                onDownload(doc)
                                                            }
                                                        >
                                                            <Download
                                                                size={14}
                                                            />
                                                        </ActionButton>
                                                        <ActionButton
                                                            label={
                                                                doc.status ===
                                                                "delete_failed"
                                                                    ? "重試刪除"
                                                                    : "刪除"
                                                            }
                                                            disabled={deleting}
                                                            destructive
                                                            onClick={() =>
                                                                onDelete(doc)
                                                            }
                                                        >
                                                            {doc.status ===
                                                            "delete_failed" ? (
                                                                <RefreshCw
                                                                    size={14}
                                                                />
                                                            ) : (
                                                                <Trash2
                                                                    size={14}
                                                                />
                                                            )}
                                                        </ActionButton>
                                                        <ActionButton
                                                            label="更多"
                                                            onClick={() =>
                                                                setExpanded(
                                                                    expandedRow
                                                                        ? null
                                                                        : doc.id,
                                                                )
                                                            }
                                                        >
                                                            <MoreHorizontal
                                                                size={14}
                                                            />
                                                        </ActionButton>
                                                    </div>
                                                    {doc.status ===
                                                        "delete_failed" && (
                                                        <Button
                                                            type="button"
                                                            onClick={() =>
                                                                onDelete(doc)
                                                            }
                                                            variant="link"
                                                            size="sm"
                                                            className="mt-1 h-auto p-0 text-xs text-red-600"
                                                        >
                                                            重試刪除
                                                        </Button>
                                                    )}
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
        </section>
    );
}
