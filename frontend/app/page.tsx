"use client";

import { useEffect, useMemo, useState } from "react";
import {
    ArrowUp,
    ChevronRight,
    CircleAlert,
    Clock3,
    Download,
    FileText,
    Library,
    Menu,
    PanelLeft,
    PenLine,
    Presentation,
    RefreshCw,
    Search,
    Sparkles,
    Upload,
    X,
} from "lucide-react";
import {
    CATEGORY_LABELS,
    Category,
    DocumentRecord,
    SlideJob,
    ApiError,
    createSlideJob,
    fetchDocuments,
    getSlideJob,
    streamChat,
    uploadDocument,
} from "../lib/api";

type View = "chat" | "files" | "slides";
type Citation = {
    document_id?: string;
    filename?: string;
    page?: number | string;
    section?: string;
};
type ChatMessage = {
    role: "user" | "assistant";
    text: string;
    citations?: Citation[];
};
const CATEGORIES = Object.keys(CATEGORY_LABELS) as Category[];

function errorMessage(error: unknown) {
    return error instanceof ApiError
        ? error.message
        : "服務暫時無法使用，請稍後再試。";
}
function formatBytes(value?: number | null) {
    if (!value) return "—";
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
function formatDate(value?: string | null) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.valueOf())
        ? value
        : new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium" }).format(
              date,
          );
}

export default function Page() {
    const [view, setView] = useState<View>("chat");
    const [collapsed, setCollapsed] = useState(false);
    const [docs, setDocs] = useState<DocumentRecord[]>([]);
    const [loadingDocs, setLoadingDocs] = useState(true);
    const [docsError, setDocsError] = useState<string | null>(null);
    const [query, setQuery] = useState("");
    const [folder, setFolder] = useState("全部文件");
    const [selected, setSelected] = useState<string[]>([]);
    const [uploadOpen, setUploadOpen] = useState(false);
    const [pending, setPending] = useState<File[]>([]);
    const [uploadCategory, setUploadCategory] = useState<Category>("bei_can");
    const [uploading, setUploading] = useState(false);
    const [chat, setChat] = useState<ChatMessage[]>([]);
    const [draft, setDraft] = useState("");
    const [scope, setScope] = useState<Category>("legislative_qa");
    const [chatBusy, setChatBusy] = useState(false);
    const [chatError, setChatError] = useState<string | null>(null);
    const [slideTitle, setSlideTitle] = useState("2026 健保政策重點整理");
    const [slideCount, setSlideCount] = useState(10);
    const [guidance, setGuidance] = useState("");
    const [tone, setTone] = useState<"formal" | "casual">("formal");
    const [slideJob, setSlideJob] = useState<SlideJob | null>(null);
    const [slideError, setSlideError] = useState<string | null>(null);
    const setTitle = setSlideTitle;
    const setCount = setSlideCount;

    const reload = async () => {
        setLoadingDocs(true);
        setDocsError(null);
        try {
            setDocs(await fetchDocuments());
        } catch (error) {
            setDocsError(errorMessage(error));
        } finally {
            setLoadingDocs(false);
        }
    };
    useEffect(() => {
        void reload();
    }, []);
    useEffect(() => {
        if (
            !docs.some((doc) =>
                ["queued", "indexing", "processing"].includes(doc.status),
            )
        )
            return;
        const timer = window.setTimeout(() => void reload(), 2500);
        return () => window.clearTimeout(timer);
    }, [docs]);
    const folders = useMemo(
        () => [
            "全部文件",
            ...Array.from(
                new Set(
                    docs
                        .map((doc) => doc.folder_name || doc.folder_id)
                        .filter(Boolean) as string[],
                ),
            ),
        ],
        [docs],
    );
    const visible = useMemo(
        () =>
            docs.filter(
                (doc) =>
                    (folder === "全部文件" ||
                        doc.folder_name === folder ||
                        doc.folder_id === folder) &&
                    (!query ||
                        doc.filename
                            .toLowerCase()
                            .includes(query.toLowerCase())),
            ),
        [docs, folder, query],
    );
    const readySelected = docs.filter(
        (doc) => selected.includes(doc.id) && doc.status === "ready",
    );

    const send = async () => {
        const question = draft.trim();
        if (!question || chatBusy) return;
        setDraft("");
        setChatError(null);
        setChat((items) => [
            ...items,
            { role: "user", text: question },
            { role: "assistant", text: "" },
        ]);
        setChatBusy(true);
        try {
            await streamChat(
                {
                    question,
                    mode: scope,
                    document_ids: selected.filter(
                        (id) =>
                            docs.find((doc) => doc.id === id)?.category ===
                            scope,
                    ),
                },
                {
                    onDelta: (delta) =>
                        setChat((items) => {
                            const next = [...items];
                            const last = next.at(-1);
                            if (last?.role === "assistant")
                                next[next.length - 1] = {
                                    ...last,
                                    text: last.text + delta,
                                };
                            return next;
                        }),
                    onDone: (citations) =>
                        setChat((items) => {
                            const next = [...items];
                            const last = next.at(-1);
                            if (last?.role === "assistant")
                                next[next.length - 1] = { ...last, citations };
                            return next;
                        }),
                },
            );
        } catch (error) {
            setChatError(errorMessage(error));
            setChat((items) => items.slice(0, -1));
        } finally {
            setChatBusy(false);
        }
    };
    const upload = async () => {
        if (!pending.length || uploading) return;
        setUploading(true);
        try {
            for (const file of pending)
                await uploadDocument(file, uploadCategory);
            setPending([]);
            setUploadOpen(false);
            await reload();
        } catch (error) {
            setDocsError(errorMessage(error));
        } finally {
            setUploading(false);
        }
    };
    const startSlides = async () => {
        if (
            !readySelected.length ||
            !slideTitle.trim() ||
            ["queued", "running"].includes(slideJob?.status || "")
        )
            return;
        setSlideError(null);
        setSlideJob({
            job_id: "",
            status: "queued",
            stage: "queued",
            message: "簡報工作已排入佇列。",
        });
        try {
            setSlideJob(
                await createSlideJob({
                    title: slideTitle.trim(),
                    document_ids: readySelected.map((doc) => doc.id),
                    slides_count: slideCount,
                    guidance,
                    tone,
                }),
            );
        } catch (error) {
            setSlideJob(null);
            setSlideError(errorMessage(error));
        }
    };
    useEffect(() => {
        const id = slideJob?.job_id;
        if (!id || ["completed", "failed"].includes(slideJob.status)) return;
        const timer = window.setTimeout(async () => {
            try {
                setSlideJob(await getSlideJob(id));
            } catch (error) {
                setSlideError(errorMessage(error));
            }
        }, 2000);
        return () => window.clearTimeout(timer);
    }, [slideJob]);

    const nav: [View, string, typeof PenLine][] = [
        ["chat", "對話", PenLine],
        ["files", "知識庫", Library],
        ["slides", "簡報生成", Presentation],
    ];
    return (
        <div className="min-h-screen bg-background text-foreground">
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
                        onClick={() => setCollapsed((value) => !value)}
                        aria-label="切換側邊欄"
                        className="ml-auto rounded-md p-2 text-muted-foreground hover:bg-accent"
                    >
                        <PanelLeft size={17} />
                    </button>
                </div>
                <nav className="flex flex-col gap-1 p-3">
                    {nav.map(([id, label, Icon]) => (
                        <button
                            key={id}
                            onClick={() => setView(id)}
                            className={`flex items-center rounded-md py-2.5 text-sm font-bold ${collapsed ? "justify-center px-2" : "gap-3 px-3"} ${view === id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent"}`}
                        >
                            <Icon size={17} />
                            {!collapsed && label}
                        </button>
                    ))}
                </nav>
                {!collapsed && (
                    <div className="mt-auto border-t border-border p-4 text-sm">
                        {docs.length} 份文件{" "}
                        <span
                            className={`float-right mt-1 size-2 rounded-full ${docsError ? "bg-amber-500" : "bg-emerald-500"}`}
                        />
                    </div>
                )}
            </aside>
            <main
                className={`min-h-screen transition-all ${collapsed ? "md:pl-20" : "md:pl-64"}`}
            >
                <header className="flex h-16 items-center border-b border-border bg-background/90 px-5">
                    <Menu className="md:hidden" size={20} />
                </header>
                {view === "chat" && (
                    <ChatView
                        chat={chat}
                        draft={draft}
                        setDraft={setDraft}
                        send={send}
                        scope={scope}
                        setScope={setScope}
                        busy={chatBusy}
                        error={chatError}
                    />
                )}
                {view === "files" && (
                    <FilesView
                        docs={visible}
                        folder={folder}
                        folders={folders}
                        setFolder={setFolder}
                        query={query}
                        setQuery={setQuery}
                        selected={selected}
                        setSelected={setSelected}
                        setUploadOpen={setUploadOpen}
                        loading={loadingDocs}
                        error={docsError}
                        reload={reload}
                    />
                )}
                {view === "slides" && (
                    <SlidesView
                        docs={docs}
                        selected={selected}
                        setSelected={setSelected}
                        title={slideTitle}
                        setTitle={setSlideTitle}
                        count={slideCount}
                        setCount={setCount}
                        guidance={guidance}
                        setGuidance={setGuidance}
                        tone={tone}
                        setTone={setTone}
                        job={slideJob}
                        error={slideError}
                        start={startSlides}
                    />
                )}
            </main>
            {uploadOpen && (
                <UploadModal
                    pending={pending}
                    setPending={setPending}
                    category={uploadCategory}
                    setCategory={setUploadCategory}
                    uploading={uploading}
                    upload={upload}
                    close={() => setUploadOpen(false)}
                />
            )}
        </div>
    );
}

function ChatView({
    chat,
    draft,
    setDraft,
    send,
    scope,
    setScope,
    busy,
    error,
}: {
    chat: ChatMessage[];
    draft: string;
    setDraft: (value: string) => void;
    send: () => void;
    scope: Category;
    setScope: (value: Category) => void;
    busy: boolean;
    error: string | null;
}) {
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
                                                (citation, i) => (
                                                    <div
                                                        className="mb-1 rounded bg-secondary px-2 py-1 text-xs"
                                                        key={`${citation.document_id || citation.filename}-${i}`}
                                                    >
                                                        {citation.filename ||
                                                            citation.document_id ||
                                                            "來源文件"}
                                                        {citation.page
                                                            ? ` · 第 ${citation.page} 頁`
                                                            : ""}
                                                        {citation.section
                                                            ? ` · ${citation.section}`
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
                <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
                    <span>搜尋範圍</span>
                    <select
                        value={scope}
                        onChange={(event) =>
                            setScope(event.target.value as Category)
                        }
                        className="rounded border border-border bg-card px-2 py-1"
                    >
                        {CATEGORIES.map((category) => (
                            <option key={category} value={category}>
                                {CATEGORY_LABELS[category]}
                            </option>
                        ))}
                    </select>
                </div>
                {error && (
                    <div className="mb-2 flex items-center gap-2 text-xs text-red-600">
                        <CircleAlert size={14} />
                        {error}
                    </div>
                )}
                <div className="flex items-end gap-2 rounded-xl border border-border bg-card p-2 shadow-sm">
                    <textarea
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
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
                        placeholder="詢問文件中的內容…"
                        rows={2}
                        className="min-h-12 flex-1 resize-none bg-transparent px-2 py-1 text-sm outline-none"
                    />
                    <button
                        onClick={send}
                        disabled={busy || !draft.trim()}
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

function FilesView({
    docs,
    folder,
    folders,
    setFolder,
    query,
    setQuery,
    selected,
    setSelected,
    setUploadOpen,
    loading,
    error,
    reload,
}: {
    docs: DocumentRecord[];
    folder: string;
    folders: string[];
    setFolder: (value: string) => void;
    query: string;
    setQuery: (value: string) => void;
    selected: string[];
    setSelected: React.Dispatch<React.SetStateAction<string[]>>;
    setUploadOpen: (value: boolean) => void;
    loading: boolean;
    error: string | null;
    reload: () => Promise<void>;
}) {
    return (
        <section className="p-5 lg:p-8">
            <div className="mb-7 flex flex-wrap items-end justify-between gap-4">
                <div>
                    <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                        知識庫 <ChevronRight size={13} /> {folder}
                    </div>
                    <h1 className="text-2xl font-semibold tracking-tight">
                        所有文件
                    </h1>
                    <p className="mt-1 text-sm text-muted-foreground">
                        管理文件、分類資料，並供生成工作流程使用。
                    </p>
                </div>
                <button
                    onClick={() => setUploadOpen(true)}
                    className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground"
                >
                    <Upload size={16} />
                    上傳文件
                </button>
            </div>
            {error && (
                <div className="mb-4 flex items-center justify-between rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    {error}
                    <button onClick={() => void reload()}>
                        <RefreshCw size={15} />
                    </button>
                </div>
            )}
            <div className="mb-5 flex flex-wrap gap-2">
                <div className="flex min-w-56 flex-1 items-center gap-2 rounded-md border border-border bg-card px-3">
                    <Search size={16} />
                    <input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="搜尋文件名稱…"
                        className="w-full bg-transparent py-2 text-sm outline-none"
                    />
                </div>
                {folders.map((value) => (
                    <button
                        onClick={() => setFolder(value)}
                        key={value}
                        className={`rounded-md border px-3 py-2 text-sm ${folder === value ? "border-primary bg-primary/10 text-primary" : "border-border bg-card text-muted-foreground"}`}
                    >
                        {value}
                    </button>
                ))}
            </div>
            <div className="overflow-hidden rounded-xl border border-border bg-card">
                <div className="border-b border-border px-4 py-3 text-xs text-muted-foreground">
                    {loading ? "載入中…" : `${docs.length} 個項目`} · 已選取{" "}
                    {selected.length} 份
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[700px] text-left text-sm">
                        <thead className="border-b border-border bg-muted/40 text-xs text-muted-foreground">
                            <tr>
                                <th className="w-10 px-4 py-3" />
                                <th className="px-3 py-3">名稱</th>
                                <th className="px-3 py-3">分類</th>
                                <th className="px-3 py-3">類型</th>
                                <th className="px-3 py-3">修改時間</th>
                                <th className="px-3 py-3">大小</th>
                                <th className="px-3 py-3">狀態</th>
                            </tr>
                        </thead>
                        <tbody>
                            {docs.map((doc) => (
                                <tr
                                    key={doc.id}
                                    className="border-b border-border last:border-0 hover:bg-muted/30"
                                >
                                    <td className="px-4 py-3">
                                        <input
                                            type="checkbox"
                                            checked={selected.includes(doc.id)}
                                            onChange={() =>
                                                setSelected((items) =>
                                                    items.includes(doc.id)
                                                        ? items.filter(
                                                              (id) =>
                                                                  id !== doc.id,
                                                          )
                                                        : [...items, doc.id],
                                                )
                                            }
                                        />
                                    </td>
                                    <td className="px-3 py-3">
                                        <div className="flex items-center gap-3">
                                            <div className="flex size-8 items-center justify-center rounded-md bg-secondary">
                                                <FileText size={16} />
                                            </div>
                                            <div>
                                                <div className="font-medium">
                                                    {doc.filename}
                                                </div>
                                                <div className="text-xs text-muted-foreground">
                                                    {doc.folder_name ||
                                                        "未分類"}
                                                </div>
                                            </div>
                                        </div>
                                    </td>
                                    <td className="px-3 py-3">
                                        <span className="rounded-full bg-secondary px-2 py-1 text-xs">
                                            {CATEGORY_LABELS[doc.category]}
                                        </span>
                                    </td>
                                    <td className="px-3 py-3 text-muted-foreground">
                                        {doc.mime_type
                                            ?.split("/")
                                            .pop()
                                            ?.toUpperCase() || "FILE"}
                                    </td>
                                    <td className="px-3 py-3 text-muted-foreground">
                                        {formatDate(
                                            doc.updated_at || doc.created_at,
                                        )}
                                    </td>
                                    <td className="px-3 py-3 text-muted-foreground">
                                        {formatBytes(doc.size_bytes)}
                                    </td>
                                    <td className="px-3 py-3">
                                        <span className="flex items-center gap-1.5 text-xs">
                                            {doc.status === "ready" ? (
                                                <span className="size-1.5 rounded-full bg-emerald-500" />
                                            ) : doc.status === "failed" ? (
                                                <CircleAlert
                                                    size={13}
                                                    className="text-red-500"
                                                />
                                            ) : (
                                                <Clock3
                                                    size={13}
                                                    className="text-amber-500"
                                                />
                                            )}
                                            {doc.status === "ready"
                                                ? "就緒"
                                                : doc.status === "failed"
                                                  ? "失敗"
                                                  : "處理中"}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {!loading && !docs.length && (
                    <div className="p-14 text-center text-sm text-muted-foreground">
                        找不到符合條件的文件
                    </div>
                )}
            </div>
        </section>
    );
}

function SlidesView({
    docs,
    selected,
    setSelected,
    title,
    setTitle,
    count,
    setCount,
    guidance,
    setGuidance,
    tone,
    setTone,
    job,
    error,
    start,
}: {
    docs: DocumentRecord[];
    selected: string[];
    setSelected: React.Dispatch<React.SetStateAction<string[]>>;
    title: string;
    setTitle: (value: string) => void;
    count: number;
    setCount: (value: number) => void;
    guidance: string;
    setGuidance: (value: string) => void;
    tone: "formal" | "casual";
    setTone: (value: "formal" | "casual") => void;
    job: SlideJob | null;
    error: string | null;
    start: () => Promise<void>;
}) {
    const ready = job?.status === "completed";
    const busy = job?.status === "queued" || job?.status === "running";
    return (
        <section className="p-5 lg:p-8">
            <div className="mb-7">
                <div className="mb-2 text-xs uppercase tracking-[.18em] text-primary">
                    Creation studio
                </div>
                <h1 className="text-2xl font-semibold tracking-tight">
                    生成簡報
                </h1>
                <p className="mt-1 text-sm text-muted-foreground">
                    選取明確的來源文件，建立結構清晰的政策簡報。
                </p>
            </div>
            {ready ? (
                <div className="max-w-4xl rounded-xl border border-border bg-card p-6">
                    <div className="mb-6 flex items-start justify-between">
                        <div>
                            <div className="text-xs text-muted-foreground">
                                生成完成
                            </div>
                            <h2 className="mt-1 text-xl font-semibold">
                                {title}
                            </h2>
                        </div>
                        <a
                            href={
                                job.download_url ||
                                `/api/v1/slides/jobs/${job.job_id}/download`
                            }
                            download
                            className="flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm"
                        >
                            <Download size={15} />
                            下載 PPTX
                        </a>
                    </div>
                    <div className="rounded-lg bg-secondary p-4 text-sm text-muted-foreground">
                        簡報已完成驗證，可以下載檔案。
                    </div>
                </div>
            ) : (
                <div className="grid max-w-5xl gap-5 xl:grid-cols-[1.1fr_.9fr]">
                    <div className="rounded-xl border border-border bg-card p-5">
                        <h2 className="mb-4 font-semibold">來源文件</h2>
                        <div className="mb-4 rounded-lg bg-secondary px-3 py-2 text-sm">
                            {selected.length} 份文件已選取（僅就緒文件可生成）
                        </div>
                        {docs
                            .filter((doc) => selected.includes(doc.id))
                            .map((doc) => (
                                <div
                                    className="mb-2 flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm"
                                    key={doc.id}
                                >
                                    <span className="flex items-center gap-2">
                                        <FileText
                                            size={15}
                                            className="text-primary"
                                        />
                                        {doc.filename}
                                    </span>
                                    <button
                                        onClick={() =>
                                            setSelected((items) =>
                                                items.filter(
                                                    (id) => id !== doc.id,
                                                ),
                                            )
                                        }
                                    >
                                        <X size={15} />
                                    </button>
                                </div>
                            ))}
                        <button
                            onClick={() =>
                                setSelected((items) =>
                                    items.length
                                        ? []
                                        : docs
                                              .filter(
                                                  (doc) =>
                                                      doc.status === "ready",
                                              )
                                              .slice(0, 3)
                                              .map((doc) => doc.id),
                                )
                            }
                            className="mt-4 text-sm text-primary"
                        >
                            {selected.length ? "清除選取" : "從知識庫選取來源"}
                        </button>
                    </div>
                    <div className="flex flex-col gap-5 rounded-xl border border-border bg-card p-5">
                        <h2 className="font-semibold">生成設定</h2>
                        <label className="flex flex-col gap-2 text-sm font-medium">
                            Presentation Title
                            <input
                                value={title}
                                onChange={(event) =>
                                    setTitle(event.target.value)
                                }
                                className="rounded-md border border-input bg-background px-3 py-2 font-normal"
                            />
                        </label>
                        <label className="flex flex-col gap-2 text-sm font-medium">
                            Number of Slides
                            <input
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
                                                Number(event.target.value) || 5,
                                            ),
                                        ),
                                    )
                                }
                                className="rounded-md border border-input bg-background px-3 py-2 font-normal"
                            />
                        </label>
                        <div className="flex flex-col gap-2 text-sm font-medium">
                            Tone
                            <div className="grid grid-cols-2 gap-2">
                                {(
                                    [
                                        ["formal", "Formal"],
                                        ["casual", "Casual"],
                                    ] as const
                                ).map(([value, label]) => (
                                    <button
                                        type="button"
                                        onClick={() => setTone(value)}
                                        className={`rounded-md border p-3 text-left ${tone === value ? "border-primary bg-primary/10" : ""}`}
                                        key={value}
                                    >
                                        {label}
                                    </button>
                                ))}
                            </div>
                        </div>
                        <label className="flex flex-col gap-2 text-sm font-medium">
                            Guidance
                            <textarea
                                value={guidance}
                                onChange={(event) =>
                                    setGuidance(event.target.value)
                                }
                                rows={4}
                                className="resize-none rounded-md border border-input bg-background px-3 py-2 font-normal"
                            />
                        </label>
                        {error && (
                            <div className="flex items-center gap-2 text-xs text-red-600">
                                <CircleAlert size={14} />
                                {error}
                            </div>
                        )}
                        {busy ? (
                            <div className="rounded-md bg-secondary p-3 text-sm">
                                <div className="mb-2 flex items-center gap-2">
                                    <Clock3 size={15} />
                                    {job.message || "正在生成中…"}
                                </div>
                                <div className="h-1.5 overflow-hidden rounded-full bg-border">
                                    <div className="h-full w-2/3 animate-pulse rounded-full bg-primary" />
                                </div>
                                <div className="mt-2 text-xs text-muted-foreground">
                                    {job.stage || "processing"}
                                </div>
                            </div>
                        ) : (
                            <button
                                onClick={() => void start()}
                                disabled={
                                    !selected.length ||
                                    !title.trim() ||
                                    !docs.some(
                                        (doc) =>
                                            selected.includes(doc.id) &&
                                            doc.status === "ready",
                                    )
                                }
                                className="flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm text-primary-foreground disabled:opacity-40"
                            >
                                <Sparkles size={16} />
                                Generate Slides
                            </button>
                        )}
                        {!selected.length && (
                            <div className="flex items-center gap-2 text-xs text-amber-600">
                                <CircleAlert size={14} />
                                請至少選取一份來源文件
                            </div>
                        )}
                    </div>
                </div>
            )}
        </section>
    );
}

function UploadModal({
    pending,
    setPending,
    category,
    setCategory,
    uploading,
    upload,
    close,
}: {
    pending: File[];
    setPending: React.Dispatch<React.SetStateAction<File[]>>;
    category: Category;
    setCategory: (value: Category) => void;
    uploading: boolean;
    upload: () => Promise<void>;
    close: () => void;
}) {
    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/30 p-4">
            <div className="w-full max-w-lg rounded-xl border border-border bg-card p-5 shadow-xl">
                <div className="mb-5 flex items-center justify-between">
                    <div>
                        <h2 className="font-semibold">上傳文件</h2>
                        <p className="mt-1 text-xs text-muted-foreground">
                            加入知識庫後，文件會在背景完成索引。
                        </p>
                    </div>
                    <button onClick={close} aria-label="關閉">
                        <X size={18} />
                    </button>
                </div>
                <label className="mb-4 flex flex-col gap-2 text-sm font-medium">
                    文件分類
                    <select
                        value={category}
                        onChange={(event) =>
                            setCategory(event.target.value as Category)
                        }
                        className="rounded-md border border-input bg-background px-3 py-2 font-normal"
                    >
                        {CATEGORIES.map((value) => (
                            <option key={value} value={value}>
                                {CATEGORY_LABELS[value]}
                            </option>
                        ))}
                    </select>
                </label>
                <label className="flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/30 text-center text-sm text-muted-foreground">
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
                                    onClick={() =>
                                        setPending((items) =>
                                            items.filter(
                                                (item) => item !== file,
                                            ),
                                        )
                                    }
                                >
                                    <X size={14} />
                                </button>
                            </div>
                        ))}
                    </div>
                )}
                <div className="mt-5 flex justify-end gap-2">
                    <button
                        onClick={close}
                        className="rounded-md border border-border px-4 py-2 text-sm"
                    >
                        取消
                    </button>
                    <button
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
