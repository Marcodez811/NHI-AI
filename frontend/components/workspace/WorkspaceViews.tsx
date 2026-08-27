"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
    ArrowUp,
    ChevronDown,
    ChevronRight,
    CircleAlert,
    CheckCircle2,
    Download,
    FileText,
    FilePlus2,
    Folder,
    FolderPlus,
    MoreHorizontal,
    Library,
    MessageCircle,
    Pencil,
    Presentation,
    RefreshCw,
    Search,
    Sparkles,
    Trash2,
    Upload,
    X,
} from "lucide-react";
import { AgentJobActivity } from "./AgentJobActivity";
import type { AgentJobClientPhase } from "../../lib/hooks/useAgentJob";
import {
    AgentJobPhase,
    CATEGORY_VALUES,
    Category,
    DocumentRead,
    DocumentStatus,
    FolderRead,
    IngestionJobRead,
    MAX_DOCUMENTS,
    MAX_QUESTION_LENGTH,
    SUPPORTED_SLIDE_EXTENSIONS,
    QaModeInfo,
    SlideJob,
    slideDownloadUrl,
} from "../../lib/api";
import type { ChatMessage, Tone, View } from "../../lib/workspace/types";

export type { ChatMessage, Tone, View } from "../../lib/workspace/types";

export function documentDisplayName(document: DocumentRead): string {
    return document.display_name || document.original_filename || document.id;
}

export function formatBytes(value?: number | null): string {
    if (!value) return "—";
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(value?: string | null): string {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.valueOf())
        ? value
        : new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium" }).format(
              date,
          );
}

export function formatDateTime(value?: string | null): string {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.valueOf())
        ? value
        : new Intl.DateTimeFormat("zh-TW", {
              dateStyle: "medium",
              timeStyle: "short",
          }).format(date);
}

export function statusLabel(status: DocumentStatus): string {
    return {
        queued: "排隊中",
        indexing: "索引中",
        ready: "就緒",
        failed: "失敗",
        deleting: "刪除中",
        delete_failed: "刪除失敗",
    }[status];
}

export function extensionOf(document: DocumentRead): string {
    const extension = document.extension?.toLowerCase();
    if (extension)
        return extension.startsWith(".") ? extension : `.${extension}`;
    const name = document.original_filename.toLowerCase();
    const dot = name.lastIndexOf(".");
    return dot >= 0 ? name.slice(dot) : "";
}

export function categoryLabel(modes: QaModeInfo[], category: Category): string {
    return modes.find((mode) => mode.mode === category)?.label ?? category;
}

function categoryDescription(modes: QaModeInfo[], category: Category): string {
    return modes.find((mode) => mode.mode === category)?.description ?? "";
}

function ActionButton({
    label,
    onClick,
    children,
    disabled = false,
    destructive = false,
}: {
    label: string;
    onClick: () => void;
    children: React.ReactNode;
    disabled?: boolean;
    destructive?: boolean;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            aria-label={label}
            title={label}
            className={`rounded-md p-1.5 text-muted-foreground hover:bg-accent disabled:cursor-not-allowed disabled:opacity-40 ${destructive ? "hover:text-red-600" : ""}`}
        >
            {children}
        </button>
    );
}

export function ChatView({
    chat,
    draft,
    setDraft,
    send,
    scope,
    setScope,
    modes,
    modesError,
    retryModes,
    busy,
    error,
    eligibilityError,
}: {
    chat: ChatMessage[];
    draft: string;
    setDraft: (value: string) => void;
    send: () => void;
    scope: Category;
    setScope: (value: Category) => void;
    modes: QaModeInfo[];
    modesError: string | null;
    retryModes: () => void;
    busy: boolean;
    error: string | null;
    eligibilityError: string | null;
}) {
    const remaining = MAX_QUESTION_LENGTH - draft.length;
    return (
        <section className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-3xl flex-col px-5">
            <div className="flex flex-1 flex-col justify-center py-12">
                {!chat.length ? (
                    <div className="text-center">
                        <h1 className="text-2xl font-semibold tracking-tight">
                            探索健保署知識庫文件
                        </h1>
                        <p className="mt-2 text-sm text-muted-foreground">
                            針對已上傳的資料提問，快速找到可信的答案
                        </p>
                        <div className="mx-auto mt-8 grid max-w-xl gap-2 sm:grid-cols-2">
                            {[
                                "整理近期健保政策相關輿情",
                                "找出立法院近期最常詢問的問題",
                                "比較不同備參資料中的政策內容",
                                "整理資料中的主要政策風險",
                            ].map((text) => (
                                <button
                                    type="button"
                                    onClick={() => setDraft(text)}
                                    className="rounded-lg border border-border bg-card p-3 text-left text-sm hover:bg-accent"
                                    key={text}
                                >
                                    {text}
                                    <ChevronRight
                                        className="float-right"
                                        size={15}
                                    />
                                </button>
                            ))}
                        </div>
                    </div>
                ) : (
                    <div className="flex flex-col gap-7">
                        {chat.map((message, index) => (
                            <div
                                key={`${index}-${message.role}`}
                                className={`flex gap-3 ${message.role === "user" ? "justify-end" : ""}`}
                            >
                                <div
                                    className={`max-w-[85%] rounded-xl px-4 py-3 text-sm leading-7 ${message.role === "user" ? "bg-primary text-primary-foreground" : "border border-border bg-card"}`}
                                >
                                    <div className="whitespace-pre-wrap">
                                        {message.text ||
                                            (busy && index === chat.length - 1
                                                ? "思考中…"
                                                : "")}
                                    </div>
                                    {message.citations?.length ? (
                                        <div className="mt-4 border-t border-border/70 pt-3">
                                            <div className="mb-2 text-xs text-muted-foreground">
                                                參考來源
                                            </div>
                                            {message.citations.map(
                                                (citation, citationIndex) => (
                                                    <div
                                                        className="mb-1 rounded bg-secondary px-2 py-1 text-xs"
                                                        key={`${citation.document_id || citation.filename || "source"}-${citationIndex}`}
                                                    >
                                                        {citation.filename ||
                                                            citation.document_id ||
                                                            "來源文件"}
                                                        {citation.page
                                                            ? ` · 第 ${citation.page} 頁`
                                                            : ""}
                                                        {citation.text
                                                            ? ` · ${citation.text}`
                                                            : ""}
                                                    </div>
                                                ),
                                            )}
                                        </div>
                                    ) : null}
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
            <div className="pb-7">
                <div className="mb-2 flex items-center justify-between gap-3 text-xs text-muted-foreground">
                    <label htmlFor="qa-scope">搜尋範圍</label>
                    <select
                        id="qa-scope"
                        value={scope}
                        onChange={(event) =>
                            setScope(event.target.value as Category)
                        }
                        className="max-w-[16rem] rounded border border-border bg-card px-2 py-1"
                        aria-label="搜尋範圍"
                    >
                        {modes.map((mode) => (
                            <option key={mode.mode} value={mode.mode}>
                                {mode.label}
                            </option>
                        ))}
                    </select>
                </div>
                {modesError && (
                    <div className="mb-2 flex items-center justify-between rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        <span>問答分類暫時無法載入，正在使用原始分類值。</span>
                        <button
                            type="button"
                            onClick={retryModes}
                            className="inline-flex items-center gap-1 underline"
                        >
                            <RefreshCw size={13} />
                            重試
                        </button>
                    </div>
                )}
                {eligibilityError && (
                    <div className="mb-2 flex items-start gap-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                        <CircleAlert size={14} className="mt-0.5 shrink-0" />
                        {eligibilityError}
                    </div>
                )}
                {error && (
                    <div className="mb-2 flex items-center gap-2 text-xs text-red-600">
                        <CircleAlert size={14} />
                        {error}
                    </div>
                )}
                <div className="mb-1 flex justify-end text-xs text-muted-foreground">
                    <span
                        className={
                            remaining < 0
                                ? "text-red-600"
                                : remaining < 1000
                                  ? "text-amber-600"
                                  : ""
                        }
                    >
                        {draft.length.toLocaleString()} /{" "}
                        {MAX_QUESTION_LENGTH.toLocaleString()}
                    </span>
                </div>
                <div className="flex items-end gap-2 rounded-xl border border-border bg-card p-2 shadow-sm">
                    <textarea
                        value={draft}
                        maxLength={MAX_QUESTION_LENGTH}
                        onChange={(event) =>
                            setDraft(
                                event.target.value.slice(
                                    0,
                                    MAX_QUESTION_LENGTH,
                                ),
                            )
                        }
                        onKeyDown={(event) => {
                            if (
                                event.key === "Enter" &&
                                !event.shiftKey &&
                                !event.nativeEvent.isComposing
                            ) {
                                event.preventDefault();
                                send();
                            }
                        }}
                        placeholder={`詢問${categoryDescription(modes, scope) || "文件"}中的內容…`}
                        rows={2}
                        className="min-h-12 flex-1 resize-none bg-transparent px-2 py-1 text-sm outline-none"
                    />
                    <button
                        type="button"
                        onClick={send}
                        disabled={
                            busy ||
                            !draft.trim() ||
                            draft.length > MAX_QUESTION_LENGTH
                        }
                        className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground disabled:opacity-40"
                        aria-label="送出"
                    >
                        <ArrowUp size={16} />
                    </button>
                </div>
            </div>
        </section>
    );
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
            <button
                type="button"
                aria-expanded={open}
                className="flex w-full items-center justify-between text-sm font-semibold"
                onClick={() => setOpen((value) => !value)}
            >
                <span className="flex items-center gap-2">
                    <Folder size={16} className="text-primary" />
                    資料夾管理
                </span>
                <ChevronDown size={15} className={open ? "rotate-180" : ""} />
            </button>
            {open && (
                <div className="mt-3 space-y-2">
                    <button
                        type="button"
                        onClick={create}
                        disabled={busy !== null}
                        className="flex w-full items-center gap-2 rounded-md border border-dashed border-border px-3 py-2 text-left text-xs text-primary hover:bg-accent"
                    >
                        <FolderPlus size={14} />
                        新增資料夾
                    </button>
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
                <button
                    type="button"
                    onClick={onUpload}
                    className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground"
                >
                    <Upload size={16} />
                    上傳文件
                </button>
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
                        <button
                            onClick={() => setFolderId(null)}
                            className={`mb-1 flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm ${!folderId ? "bg-primary/10 text-primary" : "hover:bg-accent"}`}
                        >
                            <span>全部文件</span>
                            <span>{docs.length}</span>
                        </button>
                        {folders.map((item) => (
                            <button
                                key={item.id}
                                onClick={() => setFolderId(item.id)}
                                className={`mb-1 flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm ${folderId === item.id ? "bg-primary/10 text-primary" : "hover:bg-accent"}`}
                            >
                                <span className="truncate">{item.name}</span>
                                <span>
                                    {
                                        docs.filter(
                                            (doc) => doc.folder_id === item.id,
                                        ).length
                                    }
                                </span>
                            </button>
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
                            <input
                                value={query}
                                onChange={(event) =>
                                    setQuery(event.target.value)
                                }
                                placeholder="搜尋文件名稱…"
                                className="w-full bg-transparent py-2 text-sm outline-none"
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
                                                    <select
                                                        value={doc.category}
                                                        disabled={deleting}
                                                        onChange={(event) =>
                                                            void onUpdate(
                                                                doc.id,
                                                                {
                                                                    category:
                                                                        event
                                                                            .target
                                                                            .value as Category,
                                                                },
                                                            )
                                                        }
                                                        className="max-w-28 rounded border border-input bg-background px-2 py-1 text-xs"
                                                        aria-label={`分類 ${documentDisplayName(doc)}`}
                                                    >
                                                        {modes.map((mode) => (
                                                            <option
                                                                value={
                                                                    mode.mode
                                                                }
                                                                key={mode.mode}
                                                            >
                                                                {mode.label}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </td>
                                                <td className="px-3 py-3 align-top">
                                                    <select
                                                        value={
                                                            doc.folder_id || ""
                                                        }
                                                        disabled={deleting}
                                                        onChange={(event) =>
                                                            void onUpdate(
                                                                doc.id,
                                                                {
                                                                    folder_id:
                                                                        event
                                                                            .target
                                                                            .value ||
                                                                        null,
                                                                },
                                                            )
                                                        }
                                                        className="max-w-28 rounded border border-input bg-background px-2 py-1 text-xs"
                                                        aria-label={`資料夾 ${documentDisplayName(doc)}`}
                                                    >
                                                        <option value="">
                                                            未分類
                                                        </option>
                                                        {folders.map((item) => (
                                                            <option
                                                                value={item.id}
                                                                key={item.id}
                                                            >
                                                                {item.name}
                                                            </option>
                                                        ))}
                                                    </select>
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
                                                    <button
                                                        type="button"
                                                        onClick={() =>
                                                            setExpanded(
                                                                expandedRow
                                                                    ? null
                                                                    : doc.id,
                                                            )
                                                        }
                                                        disabled={deleting}
                                                        className="flex items-center gap-1.5 text-xs"
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
                                                    </button>
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
                                                        <button
                                                            type="button"
                                                            onClick={() =>
                                                                onDelete(doc)
                                                            }
                                                            className="mt-1 text-xs text-red-600 underline"
                                                        >
                                                            重試刪除
                                                        </button>
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

export function SlidesView({
    docs,
    selected,
    setSelected,
    onUpload,
    onBrowseSources,
    title,
    setTitle,
    count,
    setCount,
    guidance,
    setGuidance,
    tone,
    setTone,
    job,
    phase,
    phaseHistory = [],
    error,
    warning,
    pollNow,
    start,
    retry,
}: {
    docs: DocumentRead[];
    selected: string[];
    setSelected: React.Dispatch<React.SetStateAction<string[]>>;
    onUpload: () => void;
    onBrowseSources: () => void;
    title: string;
    setTitle: (value: string) => void;
    count: number;
    setCount: (value: number) => void;
    guidance: string;
    setGuidance: (value: string) => void;
    tone: Tone;
    setTone: (value: Tone) => void;
    job: SlideJob | null;
    phase: AgentJobClientPhase;
    phaseHistory?: AgentJobPhase[];
    error: string | null;
    warning: string | null;
    pollNow?: () => Promise<SlideJob | undefined>;
    start: () => Promise<void>;
    retry: () => Promise<void>;
}) {
    const selectedDocs = docs.filter((doc) => selected.includes(doc.id));
    const eligible = selectedDocs.filter(
        (doc) =>
            doc.status === "ready" &&
            (SUPPORTED_SLIDE_EXTENSIONS as readonly string[]).includes(
                extensionOf(doc),
            ),
    );
    const unsupported = selectedDocs.filter(
        (doc) =>
            doc.status === "ready" &&
            !(SUPPORTED_SLIDE_EXTENSIONS as readonly string[]).includes(
                extensionOf(doc),
            ),
    );
    const notReady = selectedDocs.filter((doc) => doc.status !== "ready");
    const availableSources = docs.filter(
        (doc) =>
            doc.status === "ready" &&
            (SUPPORTED_SLIDE_EXTENSIONS as readonly string[]).includes(
                extensionOf(doc),
            ),
    );
    const [sourcePickerOpen, setSourcePickerOpen] = useState(false);
    const [sourceQuery, setSourceQuery] = useState("");
    const sourceOptions = availableSources.filter((doc) => {
        const query = sourceQuery.trim().toLowerCase();
        return (
            !query ||
            documentDisplayName(doc).toLowerCase().includes(query) ||
            doc.original_filename.toLowerCase().includes(query)
        );
    });
    const completed = job?.status === "completed";
    const failed = job?.status === "failed";
    const terminal = completed || failed;
    const busy = phase === "submitting" || phase === "polling";
    const canGenerate =
        !busy &&
        !terminal &&
        Boolean(title.trim()) &&
        selectedDocs.length > 0 &&
        notReady.length === 0 &&
        unsupported.length === 0;
    const blockedCount = selectedDocs.length - eligible.length;
    const sourceFormats = "PDF、DOCX、Markdown、TXT";
    const readinessMessage = busy
        ? "正在建立簡報，請稍候。"
        : phase === "expired"
          ? "這次簡報工作已過期，請重新生成。"
          : failed
            ? "這次簡報生成失敗，請選擇再次生成。"
            : !title.trim()
              ? "請先輸入簡報標題。"
              : !selectedDocs.length
                ? "選取至少一份可用來源後即可生成。"
                : notReady.length > 0
                  ? notReady.length + " 份選取文件正在索引，完成後才能生成。"
                  : unsupported.length > 0
                    ? "請移除不支援的來源格式後再生成。"
                    : blockedCount > 0
                      ? "請確認所有選取來源都已就緒。"
                      : "來源已就緒，可以生成。";
    const availabilityMessage = docs.length
        ? availableSources.length
            ? "知識庫目前有 " + availableSources.length + " 份可用來源。"
            : docs.some((doc) => ["queued", "indexing"].includes(doc.status))
              ? "來源正在索引，完成後會出現在可選清單。"
              : "知識庫中的文件尚未完成索引，或格式尚不支援。"
        : "知識庫目前沒有文件。";
    const downloadUrl = job ? slideDownloadUrl(job) : null;

    if (completed && downloadUrl) {
        return (
            <section className="p-5 lg:p-8">
                <div className="mx-auto max-w-5xl">
                    <div className="mb-7">
                        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
                            簡報已準備好
                        </h1>
                        <p className="mt-1 text-sm text-muted-foreground">
                            這次工作已完成。你可以下載檔案，或調整設定後再次生成。
                        </p>
                    </div>
                    <div className="max-w-3xl rounded-2xl border border-border bg-card p-6 shadow-sm">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                            <div>
                                <p className="text-xs text-muted-foreground">
                                    簡報標題
                                </p>
                                <h2 className="mt-1 text-xl font-semibold">
                                    {title}
                                </h2>
                            </div>
                            <a
                                href={downloadUrl}
                                download
                                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90"
                            >
                                <Download size={15} />
                                下載 PPTX
                            </a>
                        </div>
                        <div className="mt-6 flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                            <CheckCircle2
                                size={16}
                                className="mt-0.5 shrink-0"
                            />
                            <span>簡報已完成驗證，可以下載檔案。</span>
                        </div>
                        <dl className="mt-6 grid gap-4 border-t border-border pt-5 text-sm sm:grid-cols-3">
                            <div className="min-w-0">
                                <dt className="text-xs text-muted-foreground">來源</dt>
                                <dd
                                    className="mt-1 truncate font-medium"
                                    title={selectedDocs.map(documentDisplayName).join("、")}
                                >
                                    {selectedDocs.length
                                        ? selectedDocs.length === 1
                                            ? documentDisplayName(selectedDocs[0])
                                            : `${documentDisplayName(selectedDocs[0])} 等 ${selectedDocs.length} 份`
                                        : "—"}
                                </dd>
                            </div>
                            <div>
                                <dt className="text-xs text-muted-foreground">開始時間</dt>
                                <dd className="mt-1 font-medium">{formatDateTime(job.started_at)}</dd>
                            </div>
                            <div>
                                <dt className="text-xs text-muted-foreground">完成時間</dt>
                                <dd className="mt-1 font-medium">{formatDateTime(job.finished_at)}</dd>
                            </div>
                        </dl>
                        <button
                            type="button"
                            onClick={() => void retry()}
                            className="mt-5 rounded-md border border-border px-4 py-2 text-sm hover:bg-accent"
                        >
                            再次生成
                        </button>
                    </div>
                </div>
            </section>
        );
    }

    return (
        <section className="p-5 lg:p-8">
            <div className="mx-auto max-w-5xl">
                <div className="mb-7">
                    <h1 className="text-2xl font-semibold tracking-tight">
                        生成簡報
                    </h1>
                    <p className="mt-1 text-sm text-muted-foreground">
                        先選取來源，再調整設定，建立結構清晰的政策簡報。
                    </p>
                </div>

                <div className="grid items-start gap-5 xl:grid-cols-[1.1fr_.9fr]">
                    <div className="h-fit rounded-2xl border border-border bg-card p-6 shadow-sm">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <h2 className="font-semibold">來源文件</h2>
                                <p className="mt-1 text-sm text-muted-foreground">
                                    建立簡報前，先加入要引用的文件。
                                </p>
                            </div>
                            <span className="shrink-0 rounded-full bg-secondary px-2.5 py-1 text-xs text-muted-foreground">
                                {selectedDocs.length
                                    ? eligible.length + " 份可用"
                                    : availableSources.length + " 份可選"}
                            </span>
                        </div>

                        {selectedDocs.length > 0 && (
                            <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-border bg-secondary/60 px-3 py-2.5 text-xs">
                                <span className="text-muted-foreground">來源索引狀態</span>
                                <span className={notReady.length ? "font-medium text-amber-700" : "font-medium text-emerald-700"}>
                                    {selectedDocs.length - notReady.length} / {selectedDocs.length} 份已就緒
                                </span>
                            </div>
                        )}

                        {!selectedDocs.length ? (
                            <div className="mt-5 rounded-xl border border-dashed border-primary/30 bg-primary/[.03] p-6">
                                <div className="flex size-11 items-center justify-center rounded-xl bg-primary/10 text-primary">
                                    <FilePlus2 size={22} />
                                </div>
                                <h3 className="mt-4 font-semibold">
                                    先加入來源文件
                                </h3>
                                <p className="mt-1 max-w-md text-sm leading-6 text-muted-foreground">
                                    簡報內容會根據來源文件整理。支援{" "}
                                    {sourceFormats} 格式。
                                </p>
                                <div className="mt-5 flex flex-wrap gap-2">
                                    {availableSources.length > 0 ? (
                                        <>
                                            <button
                                                type="button"
                                                onClick={() =>
                                                    setSourcePickerOpen(true)
                                                }
                                                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90"
                                            >
                                                <Library size={15} />
                                                選取來源文件
                                            </button>
                                            <button
                                                type="button"
                                                onClick={onUpload}
                                                className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-4 py-2 text-sm hover:bg-accent"
                                            >
                                                <Upload size={15} />
                                                上傳來源文件
                                            </button>
                                        </>
                                    ) : (
                                        <>
                                            <button
                                                type="button"
                                                onClick={onUpload}
                                                className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90"
                                            >
                                                <Upload size={15} />
                                                上傳來源文件
                                            </button>
                                            <button
                                                type="button"
                                                onClick={onBrowseSources}
                                                className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-4 py-2 text-sm hover:bg-accent"
                                            >
                                                <Library size={15} />
                                                前往知識庫
                                            </button>
                                        </>
                                    )}
                                </div>
                                <p className="mt-4 text-xs text-muted-foreground">
                                    {availabilityMessage}
                                </p>
                            </div>
                        ) : (
                            <>
                                <div className="mt-5 space-y-2">
                                    {selectedDocs.map((doc) => {
                                        const supported =
                                            doc.status === "ready" &&
                                            (
                                                SUPPORTED_SLIDE_EXTENSIONS as readonly string[]
                                            ).includes(extensionOf(doc));
                                        return (
                                            <div
                                                className="rounded-lg border border-border bg-background px-3 py-3"
                                                key={doc.id}
                                            >
                                                <div className="flex items-start justify-between gap-3">
                                                    <span className="flex min-w-0 items-center gap-2 text-sm">
                                                        <FileText
                                                            size={15}
                                                            className="shrink-0 text-primary"
                                                        />
                                                        <span className="truncate">
                                                            {documentDisplayName(
                                                                doc,
                                                            )}
                                                        </span>
                                                    </span>
                                                    <button
                                                        type="button"
                                                        onClick={() =>
                                                            setSelected(
                                                                (items) =>
                                                                    items.filter(
                                                                        (id) =>
                                                                            id !==
                                                                            doc.id,
                                                                    ),
                                                            )
                                                        }
                                                        aria-label={
                                                            "移除 " +
                                                            documentDisplayName(
                                                                doc,
                                                            )
                                                        }
                                                        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                                                    >
                                                        <X size={15} />
                                                    </button>
                                                </div>
                                                {doc.status !== "ready" && (
                                                    <p className="mt-1 pl-6 text-xs text-amber-600">
                                                        {statusLabel(
                                                            doc.status,
                                                        )}
                                                        ，完成索引後才可使用
                                                    </p>
                                                )}
                                                {doc.status === "ready" &&
                                                    !supported && (
                                                        <p className="mt-1 pl-6 text-xs text-red-600">
                                                            不支援的來源格式（
                                                            {extensionOf(doc) ||
                                                                "未知"}
                                                            ）
                                                        </p>
                                                    )}
                                            </div>
                                        );
                                    })}
                                </div>
                                <div className="mt-4 flex flex-wrap gap-2">
                                    <button
                                        type="button"
                                        onClick={() =>
                                            setSourcePickerOpen(true)
                                        }
                                        className="rounded-md border border-border px-3 py-2 text-sm hover:bg-accent"
                                    >
                                        新增來源
                                    </button>
                                    <button
                                        type="button"
                                        onClick={onBrowseSources}
                                        className="rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
                                    >
                                        管理知識庫
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setSelected([])}
                                        className="rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
                                    >
                                        清除選取
                                    </button>
                                </div>
                            </>
                        )}

                        {sourcePickerOpen && (
                            <div className="mt-5 rounded-xl border border-border bg-background p-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <h3 className="text-sm font-semibold">
                                            選擇來源文件
                                        </h3>
                                        <p className="mt-1 text-xs text-muted-foreground">
                                            只顯示已完成索引且格式支援的文件。
                                        </p>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() =>
                                            setSourcePickerOpen(false)
                                        }
                                        aria-label="關閉來源選擇"
                                        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                                    >
                                        <X size={15} />
                                    </button>
                                </div>
                                <div className="mt-3 flex items-center gap-2 rounded-md border border-border bg-card px-3">
                                    <Search
                                        size={15}
                                        className="text-muted-foreground"
                                    />
                                    <input
                                        value={sourceQuery}
                                        onChange={(event) =>
                                            setSourceQuery(event.target.value)
                                        }
                                        placeholder="搜尋文件名稱…"
                                        aria-label="搜尋來源文件"
                                        className="w-full bg-transparent py-2 text-sm outline-none"
                                    />
                                </div>
                                <div className="mt-3 max-h-56 space-y-1 overflow-y-auto">
                                    {sourceOptions.map((doc) => (
                                        <label
                                            key={doc.id}
                                            className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-sm hover:bg-accent"
                                        >
                                            <input
                                                type="checkbox"
                                                checked={selected.includes(
                                                    doc.id,
                                                )}
                                                onChange={(event) =>
                                                    setSelected((items) =>
                                                        event.target.checked
                                                            ? Array.from(
                                                                  new Set([
                                                                      ...items,
                                                                      doc.id,
                                                                  ]),
                                                              ).slice(
                                                                  0,
                                                                  MAX_DOCUMENTS,
                                                              )
                                                            : items.filter(
                                                                  (id) =>
                                                                      id !==
                                                                      doc.id,
                                                              ),
                                                    )
                                                }
                                            />
                                            <FileText
                                                size={15}
                                                className="shrink-0 text-primary"
                                            />
                                            <span className="min-w-0 truncate">
                                                {documentDisplayName(doc)}
                                            </span>
                                        </label>
                                    ))}
                                    {!sourceOptions.length && (
                                        <p className="px-2 py-4 text-center text-xs text-muted-foreground">
                                            找不到符合條件的可用文件。
                                        </p>
                                    )}
                                </div>
                                <div className="mt-3 flex items-center justify-between gap-3 border-t border-border pt-3 text-xs text-muted-foreground">
                                    <span>
                                        已選取 {selected.length} /{" "}
                                        {MAX_DOCUMENTS}
                                    </span>
                                    <button
                                        type="button"
                                        onClick={() =>
                                            setSourcePickerOpen(false)
                                        }
                                        className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground hover:bg-primary/90"
                                    >
                                        完成
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="flex h-fit flex-col gap-5 rounded-2xl border border-border bg-card p-6 shadow-sm">
                        <div>
                            <h2 className="font-semibold">生成設定</h2>
                            <p className="mt-1 text-sm text-muted-foreground">
                                調整簡報標題、頁數與語氣。
                            </p>
                        </div>

                        <div className="space-y-5">
                            <label
                                htmlFor="slide-title"
                                className="flex flex-col gap-2 text-sm font-medium"
                            >
                                簡報標題
                                <input
                                    id="slide-title"
                                    value={title}
                                    onChange={(event) =>
                                        setTitle(event.target.value)
                                    }
                                    className="rounded-md border border-input bg-background px-3 py-2 font-normal"
                                />
                            </label>
                            <label
                                htmlFor="slide-count"
                                className="flex flex-col gap-2 text-sm font-medium"
                            >
                                投影片張數
                                <input
                                    id="slide-count"
                                    type="number"
                                    min={5}
                                    max={25}
                                    value={count}
                                    onChange={(event) =>
                                        setCount(
                                            Math.min(
                                                25,
                                                Math.max(
                                                    5,
                                                    Number(
                                                        event.target.value,
                                                    ) || 5,
                                                ),
                                            ),
                                        )
                                    }
                                    className="rounded-md border border-input bg-background px-3 py-2 font-normal"
                                />
                            </label>
                            <div className="flex flex-col gap-2 text-sm font-medium">
                                語氣
                                <div
                                    role="group"
                                    aria-label="語氣"
                                    className="grid grid-cols-2 gap-2"
                                >
                                    {(
                                        [
                                            ["formal", "正式"],
                                            ["casual", "輕鬆"],
                                        ] as const
                                    ).map(([value, label]) => (
                                        <button
                                            type="button"
                                            aria-pressed={tone === value}
                                            onClick={() => setTone(value)}
                                            className={
                                                tone === value
                                                    ? "rounded-md border border-primary bg-primary/10 p-3 text-left text-primary transition-colors"
                                                    : "rounded-md border border-border p-3 text-left transition-colors hover:bg-accent"
                                            }
                                            key={value}
                                        >
                                            {label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                            <label
                                htmlFor="slide-guidance"
                                className="flex flex-col gap-2 text-sm font-medium"
                            >
                                補充指引{" "}
                                <span className="font-normal text-muted-foreground">
                                    選填
                                </span>
                                <textarea
                                    id="slide-guidance"
                                    value={guidance}
                                    onChange={(event) =>
                                        setGuidance(event.target.value)
                                    }
                                    rows={4}
                                    placeholder="例如：聚焦政策影響，以主管簡報語氣撰寫。"
                                    className="resize-none rounded-md border border-input bg-background px-3 py-2 font-normal"
                                />
                            </label>
                        </div>

                        {error && !job && (
                            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-xs text-red-700">
                                <CircleAlert size={14} />
                                {error}
                            </div>
                        )}
                        {warning && !job && (
                            <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-xs text-amber-800">
                                <CircleAlert size={14} className="mt-0.5 shrink-0" />
                                <div className="min-w-0 flex-1">
                                    <p>{warning}</p>
                                    {pollNow && (
                                        <button
                                            type="button"
                                            onClick={() => void pollNow()}
                                            className="mt-1.5 font-medium underline underline-offset-2"
                                        >
                                            立即重試
                                        </button>
                                    )}
                                </div>
                            </div>
                        )}
                        {job && (busy || failed || completed) && (
                            <AgentJobActivity
                                job={job}
                                clientPhase={phase}
                                phaseHistory={phaseHistory}
                                warning={warning}
                                error={error}
                                workflowLabel="簡報"
                                onRefresh={pollNow ? () => void pollNow() : undefined}
                                onRetry={() => void retry()}
                            />
                        )}
                        {phase === "expired" && (
                            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                                簡報工作已過期或不存在，請重新生成。
                            </div>
                        )}

                        <div
                            aria-live="polite"
                            className={
                                canGenerate
                                    ? "flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-3 text-sm text-emerald-800"
                                    : "flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800"
                            }
                        >
                            {canGenerate ? (
                                <CheckCircle2
                                    size={16}
                                    className="mt-0.5 shrink-0"
                                />
                            ) : (
                                <CircleAlert
                                    size={16}
                                    className="mt-0.5 shrink-0"
                                />
                            )}
                            <span>{readinessMessage}</span>
                        </div>
                        <button
                            type="button"
                            onClick={() => void start()}
                            disabled={!canGenerate}
                            className={
                                canGenerate
                                    ? "flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
                                    : "flex cursor-not-allowed items-center justify-center gap-2 rounded-md bg-muted px-4 py-2.5 text-sm font-medium text-muted-foreground"
                            }
                        >
                            <Sparkles size={16} />
                            {phase === "submitting" ? "送出中…" : "生成簡報"}
                        </button>
                        {selected.length > 0 &&
                            (notReady.length > 0 || unsupported.length > 0) && (
                                <p className="text-xs text-muted-foreground">
                                    {notReady.length
                                        ? notReady.length + " 份文件尚未就緒，完成索引後才能生成"
                                        : ""}
                                    {unsupported.length
                                        ? (notReady.length ? "；" : "") +
                                          unsupported.length +
                                          " 份文件格式不支援，請先移除"
                                        : ""}
                                    。
                                </p>
                            )}
                    </div>
                </div>
            </div>
        </section>
    );
}

export function UploadModal({
    pending,
    setPending,
    category,
    setCategory,
    folderId,
    setFolderId,
    folders,
    modes,
    uploading,
    upload,
    close,
}: {
    pending: File[];
    setPending: React.Dispatch<React.SetStateAction<File[]>>;
    category: Category;
    setCategory: (value: Category) => void;
    folderId: string | null;
    setFolderId: (value: string | null) => void;
    folders: FolderRead[];
    modes: QaModeInfo[];
    uploading: boolean;
    upload: () => Promise<void>;
    close: () => void;
}) {
    const closeButton = useRef<HTMLButtonElement>(null);

    useEffect(() => {
        closeButton.current?.focus();
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") close();
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, []);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4">
            <div
                role="dialog"
                aria-modal="true"
                aria-labelledby="upload-dialog-title"
                className="w-full max-w-lg rounded-xl border border-border bg-card p-5 shadow-xl"
            >
                <div className="mb-5 flex items-center justify-between">
                    <div>
                        <h2 id="upload-dialog-title" className="font-semibold">
                            上傳文件
                        </h2>
                        <p className="mt-1 text-xs text-muted-foreground">
                            加入知識庫後，文件會在背景完成索引。
                        </p>
                    </div>
                    <button
                        type="button"
                        ref={closeButton}
                        onClick={close}
                        aria-label="關閉"
                    >
                        <X size={18} />
                    </button>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                    <label className="flex flex-col gap-2 text-sm font-medium">
                        文件分類
                        <select
                            value={category}
                            onChange={(event) =>
                                setCategory(event.target.value as Category)
                            }
                            className="rounded-md border border-input bg-background px-3 py-2 font-normal"
                        >
                            {modes.map((mode) => (
                                <option key={mode.mode} value={mode.mode}>
                                    {mode.label}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="flex flex-col gap-2 text-sm font-medium">
                        資料夾
                        <select
                            value={folderId || ""}
                            onChange={(event) =>
                                setFolderId(event.target.value || null)
                            }
                            className="rounded-md border border-input bg-background px-3 py-2 font-normal"
                        >
                            <option value="">未分類</option>
                            {folders.map((folder) => (
                                <option value={folder.id} key={folder.id}>
                                    {folder.name}
                                </option>
                            ))}
                        </select>
                    </label>
                </div>
                <label className="mt-4 flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/30 text-center text-sm text-muted-foreground">
                    <Upload size={22} />
                    <span>點擊選取一或多份文件</span>
                    <span className="text-xs">PDF、DOCX、Markdown、TXT</span>
                    <input
                        type="file"
                        multiple
                        accept=".pdf,.docx,.md,.markdown,.txt"
                        className="hidden"
                        onChange={(event) =>
                            setPending(Array.from(event.target.files || []))
                        }
                    />
                </label>
                {pending.length > 0 && (
                    <div className="mt-4 flex flex-col gap-2">
                        {pending.map((file) => (
                            <div
                                className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm"
                                key={`${file.name}-${file.size}`}
                            >
                                <span className="flex items-center gap-2">
                                    <FileText size={15} />
                                    {file.name}
                                </span>
                                <button
                                    type="button"
                                    onClick={() =>
                                        setPending((items) =>
                                            items.filter(
                                                (item) => item !== file,
                                            ),
                                        )
                                    }
                                    aria-label={`移除 ${file.name}`}
                                >
                                    <X size={14} />
                                </button>
                            </div>
                        ))}
                    </div>
                )}
                <div className="mt-5 flex justify-end gap-2">
                    <button
                        type="button"
                        onClick={close}
                        className="rounded-md border border-border px-4 py-2 text-sm"
                    >
                        取消
                    </button>
                    <button
                        type="button"
                        disabled={!pending.length || uploading}
                        onClick={() => void upload()}
                        className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-40"
                    >
                        {uploading ? "上傳中…" : "開始上傳"}
                    </button>
                </div>
            </div>
        </div>
    );
}

export function Sidebar({
    view,
    setView,
    collapsed,
    setCollapsed,
    documentCount,
    hasError,
}: {
    view: View;
    setView: (view: View) => void;
    collapsed: boolean;
    setCollapsed: (value: boolean) => void;
    documentCount: number;
    hasError: boolean;
}) {
    const navItems = [
        ["chat", "對話", MessageCircle],
        ["files", "知識庫", Library],
        ["slides", "簡報生成", Presentation],
    ] as const;
    return (
        <>
            <aside
                className={`fixed inset-y-0 left-0 z-20 hidden border-r border-border bg-sidebar transition-all md:flex md:flex-col ${collapsed ? "w-20" : "w-64"}`}
            >
                <div
                    className={`flex h-16 items-center border-b border-border ${collapsed ? "justify-center px-3" : "gap-3 px-5"}`}
                >
                    <div className={collapsed ? "hidden" : ""}>
                        <div className="text-lg font-semibold tracking-tight">
                            健保署 AI
                        </div>
                        <div className="text-[10px] uppercase tracking-[.18em] text-muted-foreground">
                            Powered By FlySheet
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => setCollapsed(!collapsed)}
                        aria-label="切換側邊欄"
                        className={`${collapsed ? "" : "ml-auto"} rounded-md p-2 text-muted-foreground hover:bg-accent`}
                    >
                        <ChevronRight
                            size={17}
                            className={collapsed ? "" : "rotate-180"}
                        />
                    </button>
                </div>
                <nav className="flex flex-col gap-1 p-3" aria-label="主要導覽">
                    {navItems.map(([id, label, Icon]) => (
                        <button
                            type="button"
                            key={id}
                            onClick={() => setView(id)}
                            aria-label={label}
                            aria-current={view === id ? "page" : undefined}
                            className={`flex items-center rounded-md py-2.5 text-sm font-bold ${collapsed ? "justify-center px-2" : "gap-3 px-3"} ${view === id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent"}`}
                        >
                            <Icon size={17} aria-hidden="true" />
                            {!collapsed && label}
                        </button>
                    ))}
                </nav>
                {!collapsed && (
                    <div className="mt-auto border-t border-border p-4 text-sm">
                        {documentCount} 份文件{" "}
                        <span
                            className={`float-right mt-1 size-2 rounded-full ${hasError ? "bg-amber-500" : "bg-emerald-500"}`}
                            aria-label={
                                hasError ? "有待處理的錯誤" : "系統正常"
                            }
                        />
                    </div>
                )}
            </aside>
            <nav
                className="fixed inset-x-3 bottom-3 z-30 grid grid-cols-3 gap-1 rounded-2xl border border-border bg-card p-1.5 shadow-lg md:hidden"
                aria-label="主要導覽"
            >
                {navItems.map(([id, label, Icon]) => (
                    <button
                        type="button"
                        key={id}
                        onClick={() => setView(id)}
                        aria-label={label}
                        aria-current={view === id ? "page" : undefined}
                        className={`flex min-h-12 flex-col items-center justify-center gap-1 rounded-xl px-2 text-[11px] font-semibold ${view === id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent"}`}
                    >
                        <Icon size={17} aria-hidden="true" />
                        <span>{label}</span>
                    </button>
                ))}
            </nav>
        </>
    );
}
