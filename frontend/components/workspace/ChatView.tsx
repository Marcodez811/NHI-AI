"use client";

import * as React from "react";

import { ArrowUp, Check, ChevronRight, CircleAlert, FileText, RefreshCw, Search } from "lucide-react";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import type { Category, QaModeInfo } from "../../lib/api/chat";
import type { DocumentRead } from "../../lib/api/documents";
import { MAX_DOCUMENTS, documentDisplayName } from "../../lib/api/documents";
import type { RetrievalStatus } from "../../lib/api/retrieval";
import { MAX_QUESTION_LENGTH } from "../../lib/api/chat";
import type { ChatMessage } from "../../lib/workspace/types";
import { ChatEmptyState } from "./ChatEmptyState";
import { CategorySelect } from "./DocumentSelects";
import { isChatComposerBlocked, resolveChatEmptyState } from "./chat-state";

function categoryDescription(modes: QaModeInfo[], category: Category): string {
    return modes.find((mode) => mode.mode === category)?.description ?? "";
}

function ScopePicker({
    scope,
    setScope,
    modes,
}: {
    scope: Category;
    setScope: (value: Category) => void;
    modes: QaModeInfo[];
}) {
    return (
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
            <label htmlFor="qa-scope">搜尋範圍</label>
            <CategorySelect
                modes={modes}
                value={scope}
                onValueChange={(value) => {
                    setScope(value as Category);
                }}
                id="qa-scope"
                className="max-w-[16rem]"
                aria-label="搜尋範圍"
            />
        </div>
    );
}

function ChatSourcePicker({
    docs,
    selected,
    setSelected,
    scope,
}: {
    docs: DocumentRead[];
    selected: string[];
    setSelected: React.Dispatch<React.SetStateAction<string[]>>;
    scope: Category;
}) {
    const [open, setOpen] = React.useState(false);
    const [query, setQuery] = React.useState("");
    const available = docs.filter((doc) => {
        const q = query.trim().toLowerCase();
        return doc.category === scope && doc.status === "ready" && doc.retrieval_enabled && (!q || documentDisplayName(doc).toLowerCase().includes(q) || doc.original_filename.toLowerCase().includes(q));
    });
    return (
        <>
            <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
                <span>來源文件</span>
                <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)} className="h-7 gap-1.5 text-xs">
                    <FileText size={13} />
                    {selected.length ? `${selected.length} 份文件` : "全部可用文件"}
                </Button>
            </div>
            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle>選擇對話來源</DialogTitle>
                        <DialogDescription>只顯示目前搜尋範圍中已完成索引且啟用檢索的文件。未選取時會搜尋全部可用文件。</DialogDescription>
                    </DialogHeader>
                    <div className="flex items-center gap-2 rounded-md border border-border bg-card px-3">
                        <Search size={15} className="text-muted-foreground" />
                        <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋文件名稱…" aria-label="搜尋對話來源" className="h-8 border-0 bg-transparent py-2 text-sm shadow-none focus-visible:ring-0" />
                    </div>
                    <div className="max-h-64 space-y-1 overflow-y-auto">
                        {available.map((doc) => {
                            const checked = selected.includes(doc.id);
                            const limitReached = selected.length >= MAX_DOCUMENTS && !checked;
                            return (
                                <label key={doc.id} className={`flex items-center gap-3 rounded-md px-2 py-2 text-sm ${limitReached ? "cursor-not-allowed opacity-45" : "cursor-pointer hover:bg-accent"}`}>
                                    <input type="checkbox" checked={checked} disabled={limitReached} onChange={(event) => setSelected((items) => event.target.checked ? [...items, doc.id].slice(0, MAX_DOCUMENTS) : items.filter((id) => id !== doc.id))} />
                                    <FileText size={15} className="shrink-0 text-primary" />
                                    <span className="min-w-0 flex-1 truncate">{documentDisplayName(doc)}</span>
                                    {checked && <Check size={14} className="text-primary" aria-hidden="true" />}
                                </label>
                            );
                        })}
                        {!available.length && <p className="py-5 text-center text-xs text-muted-foreground">找不到符合條件的可用文件。</p>}
                    </div>
                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={() => setSelected([])} disabled={!selected.length}>清除選取</Button>
                        <Button type="button" onClick={() => setOpen(false)}>完成</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}

const SUGGESTED_QUESTIONS = [
    "整理近期健保政策相關輿情",
    "找出立法院近期最常詢問的問題",
    "比較不同備參資料中的政策內容",
    "整理資料中的主要政策風險",
] as const;

function ChatWelcome({ setDraft }: { setDraft: (value: string) => void }) {
    return (
        <div className="text-center">
            <h1 className="text-2xl font-semibold tracking-tight">探索健保署知識庫文件</h1>
            <p className="mt-2 text-sm text-muted-foreground">
                針對已上傳的資料提問，快速找到可信的答案
            </p>
            <div className="mx-auto mt-8 grid max-w-xl gap-2 sm:grid-cols-2">
                {SUGGESTED_QUESTIONS.map((text) => (
                    <Button
                        type="button"
                        onClick={() => setDraft(text)}
                        variant="outline"
                        className="h-auto justify-between p-3 text-left text-sm"
                        key={text}
                    >
                        {text}
                        <ChevronRight size={15} />
                    </Button>
                ))}
            </div>
        </div>
    );
}

function ChatConversation({
    chat,
    busy,
}: {
    chat: ChatMessage[];
    busy: boolean;
}) {
    return (
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
                            {message.text || (busy && index === chat.length - 1 ? "思考中…" : "")}
                        </div>
                        {message.citations?.length ? (
                            <div className="mt-4 border-t border-border/70 pt-3">
                                <div className="mb-2 text-xs text-muted-foreground">參考來源</div>
                                {message.citations.map((citation, citationIndex) => (
                                    <div
                                        className="mb-1 rounded bg-secondary px-2 py-1 text-xs"
                                        key={`${citation.document_id || citation.filename || "source"}-${citationIndex}`}
                                    >
                                        {citation.filename || citation.document_id || "來源文件"}
                                        {citation.page ? ` · 第 ${citation.page} 頁` : ""}
                                        {citation.text ? ` · ${citation.text}` : ""}
                                    </div>
                                ))}
                            </div>
                        ) : null}
                    </div>
                </div>
            ))}
        </div>
    );
}

function ChatComposer({
    draft,
    setDraft,
    send,
    scope,
    setScope,
    modes,
    modesError,
    retryModes,
    busy,
    blocked,
    error,
    eligibilityError,
    docs,
    selected,
    setSelected,
}: {
    draft: string;
    setDraft: (value: string) => void;
    send: () => void;
    scope: Category;
    setScope: (value: Category) => void;
    modes: QaModeInfo[];
    modesError: string | null;
    retryModes: () => void;
    busy: boolean;
    blocked: boolean;
    error: string | null;
    eligibilityError: string | null;
    docs: DocumentRead[];
    selected: string[];
    setSelected: React.Dispatch<React.SetStateAction<string[]>>;
}) {
    const remaining = MAX_QUESTION_LENGTH - draft.length;
    return (
        <div className="pb-7">
            <div className="mb-2 space-y-2">
                <ScopePicker
                    scope={scope}
                    setScope={(value) => { setScope(value); setSelected([]); }}
                    modes={modes}
                />
                {docs.length > 0 && (
                    <ChatSourcePicker
                        docs={docs}
                        selected={selected}
                        setSelected={setSelected}
                        scope={scope}
                    />
                )}
            </div>
            {modesError && (
                <div className="mb-2 flex items-center justify-between rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                    <span>問答分類暫時無法載入，正在使用原始分類值。</span>
                    <Button type="button" onClick={retryModes} variant="link" size="sm">
                        <RefreshCw size={13} />
                        重試
                    </Button>
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
                <span className={remaining < 0 ? "text-red-600" : remaining < 1000 ? "text-amber-600" : ""}>
                    {draft.length.toLocaleString()} / {MAX_QUESTION_LENGTH.toLocaleString()}
                </span>
            </div>
            <div className="flex items-end gap-2 rounded-xl border border-border bg-card p-2 shadow-sm">
                <Textarea
                    value={draft}
                    maxLength={MAX_QUESTION_LENGTH}
                    onChange={(event) => setDraft(event.target.value.slice(0, MAX_QUESTION_LENGTH))}
                    onKeyDown={(event) => {
                        if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                        event.preventDefault();
                        send();
                    }}
                    placeholder={`詢問${categoryDescription(modes, scope) || "文件"}中的內容…`}
                    rows={2}
                    className="min-h-12 flex-1 resize-none border-0 bg-transparent px-2 py-1 text-sm shadow-none focus-visible:ring-0"
                />
                <Button
                    type="button"
                    onClick={send}
                    disabled={busy || blocked || !draft.trim() || draft.length > MAX_QUESTION_LENGTH}
                    variant="default"
                    size="icon"
                    aria-label="送出"
                >
                    <ArrowUp size={16} />
                </Button>
            </div>
        </div>
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
    retrievalStatus,
    retrievalLoading = false,
    retrievalError,
    retryRetrieval,
    catalogLoading = false,
    hasPendingDocuments = false,
    hasAnyReadyDocuments = true,
    hasCategoryReadyDocuments = true,
    onUploadSources,
    docs = [],
    selected = [],
    setSelected = () => undefined,
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
    /** Omit for legacy presentational consumers that do not own readiness. */
    retrievalStatus?: RetrievalStatus | null;
    retrievalLoading?: boolean;
    retrievalError?: string | null;
    retryRetrieval?: () => void;
    catalogLoading?: boolean;
    hasPendingDocuments?: boolean;
    hasAnyReadyDocuments?: boolean;
    hasCategoryReadyDocuments?: boolean;
    onUploadSources?: () => void;
    docs?: DocumentRead[];
    selected?: string[];
    setSelected?: React.Dispatch<React.SetStateAction<string[]>>;
}) {
    const ownsRetrievalState = retrievalStatus !== undefined;
    const emptyStateKind = resolveChatEmptyState({
        ownsRetrievalState,
        retrievalStatus,
        retrievalLoading,
        retrievalError,
        catalogLoading,
        hasPendingDocuments,
        hasAnyReadyDocuments,
        hasCategoryReadyDocuments,
    });
    const composerBlocked = isChatComposerBlocked(emptyStateKind);
    return (
        <section className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-3xl flex-col px-5">
            <div className="flex flex-1 flex-col justify-center py-12">
                {!chat.length ? (
                    emptyStateKind ? (
                        <div className="space-y-5">
                            <ScopePicker
                                scope={scope}
                                setScope={(value) => { setScope(value); setSelected([]); }}
                                modes={modes}
                            />
                            {docs.length > 0 && <ChatSourcePicker docs={docs} selected={selected} setSelected={setSelected} scope={scope} />}
                            <ChatEmptyState
                                kind={emptyStateKind}
                                scopeLabel={
                                    modes.find((mode) => mode.mode === scope)
                                        ?.label
                                }
                                onUpload={onUploadSources}
                                onRetry={retryRetrieval}
                            />
                        </div>
                    ) : <ChatWelcome setDraft={setDraft} />
                ) : <ChatConversation chat={chat} busy={busy} />}
            </div>
            {!(!chat.length && composerBlocked) && (
                <ChatComposer
                    draft={draft}
                    setDraft={setDraft}
                    send={send}
                    scope={scope}
                    setScope={setScope}
                    modes={modes}
                    modesError={modesError}
                    retryModes={retryModes}
                    busy={busy}
                    blocked={composerBlocked}
                    error={error}
                    eligibilityError={eligibilityError}
                    docs={docs}
                    selected={selected}
                    setSelected={setSelected}
                />
            )}
        </section>
    );
}
