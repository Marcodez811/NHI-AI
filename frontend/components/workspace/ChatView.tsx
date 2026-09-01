"use client";

import { ArrowUp, ChevronRight, CircleAlert, RefreshCw } from "lucide-react";
import { Button } from "../ui/button";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "../ui/select";
import { Textarea } from "../ui/textarea";
import type { Category, QaModeInfo } from "../../lib/api/chat";
import type { RetrievalStatus } from "../../lib/api/retrieval";
import { MAX_QUESTION_LENGTH } from "../../lib/api/chat";
import type { ChatMessage } from "../../lib/workspace/types";
import { ChatEmptyState, type ChatEmptyStateKind } from "./ChatEmptyState";

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
            <Select
                value={scope}
                onValueChange={(value) => setScope(value as Category)}
            >
                <SelectTrigger
                    id="qa-scope"
                    className="max-w-[16rem]"
                    aria-label="搜尋範圍"
                >
                    <SelectValue />
                </SelectTrigger>
                <SelectContent>
                    {modes.map((mode) => (
                        <SelectItem key={mode.mode} value={mode.mode}>
                            {mode.label}
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
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
}) {
    const remaining = MAX_QUESTION_LENGTH - draft.length;
    const ownsRetrievalState = retrievalStatus !== undefined;
    let emptyStateKind: ChatEmptyStateKind | null = null;
    if (ownsRetrievalState) {
        if (retrievalError) {
            emptyStateKind = "error";
        } else if (
            retrievalLoading ||
            catalogLoading ||
            hasPendingDocuments ||
            !retrievalStatus ||
            retrievalStatus.state === "uninitialized" ||
            retrievalStatus.state === "provisioning"
        ) {
            emptyStateKind = "loading";
        } else if (
            retrievalStatus.state === "error" ||
            !retrievalStatus.can_retrieve
        ) {
            emptyStateKind = "error";
        } else if (!hasAnyReadyDocuments) {
            emptyStateKind = "empty";
        } else if (!hasCategoryReadyDocuments) {
            emptyStateKind = "category-empty";
        }
    }
    const composerBlocked = Boolean(
        ownsRetrievalState &&
        (emptyStateKind === "loading" ||
            emptyStateKind === "error" ||
            emptyStateKind === "empty" ||
            emptyStateKind === "category-empty"),
    );
    return (
        <section className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-3xl flex-col px-5">
            <div className="flex flex-1 flex-col justify-center py-12">
                {!chat.length ? (
                    emptyStateKind ? (
                        <div className="space-y-5">
                            <ScopePicker
                                scope={scope}
                                setScope={setScope}
                                modes={modes}
                            />
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
                    ) : (
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
                    )
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
            {!(!chat.length && composerBlocked) && (
                <div className="pb-7">
                    <div className="mb-2">
                        <ScopePicker
                            scope={scope}
                            setScope={setScope}
                            modes={modes}
                        />
                    </div>
                    {modesError && (
                        <div className="mb-2 flex items-center justify-between rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                            <span>
                                問答分類暫時無法載入，正在使用原始分類值。
                            </span>
                            <Button
                                type="button"
                                onClick={retryModes}
                                variant="link"
                                size="sm"
                            >
                                <RefreshCw size={13} />
                                重試
                            </Button>
                        </div>
                    )}
                    {eligibilityError && (
                        <div className="mb-2 flex items-start gap-2 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                            <CircleAlert
                                size={14}
                                className="mt-0.5 shrink-0"
                            />
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
                        <Textarea
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
                            className="min-h-12 flex-1 resize-none border-0 bg-transparent px-2 py-1 text-sm shadow-none focus-visible:ring-0"
                        />
                        <Button
                            type="button"
                            onClick={send}
                            disabled={
                                busy ||
                                composerBlocked ||
                                !draft.trim() ||
                                draft.length > MAX_QUESTION_LENGTH
                            }
                            variant="default"
                            size="icon"
                            aria-label="送出"
                        >
                            <ArrowUp size={16} />
                        </Button>
                    </div>
                </div>
            )}
        </section>
    );
}
