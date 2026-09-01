"use client";

import {
    ArrowRight,
    CircleAlert,
    LibraryBig,
    LoaderCircle,
    RefreshCw,
    Upload,
} from "lucide-react";
import { Button } from "../ui/button";
import {
    Empty,
    EmptyContent,
    EmptyDescription,
    EmptyHeader,
    EmptyMedia,
    EmptyTitle,
} from "../ui/empty";

export type ChatEmptyStateKind = "loading" | "error" | "empty" | "category-empty";

const copy: Record<
    Exclude<ChatEmptyStateKind, "loading">,
    { title: string; description: string }
> = {
    empty: {
        title: "先上傳第一份文件",
        description: "將健保政策資料加入知識庫，完成索引後即可提出有依據的問題。",
    },
    "category-empty": {
        title: "此搜尋範圍尚無可用文件",
        description: "切換搜尋範圍，或上傳屬於目前分類的文件後即可開始提問。",
    },
    error: {
        title: "知識庫尚未就緒",
        description: "暫時無法確認檢索服務狀態。請稍後重試，或先前往知識庫檢查文件。",
    },
};

export function ChatEmptyState({
    kind,
    scopeLabel,
    onUpload,
    onRetry,
}: {
    kind: ChatEmptyStateKind;
    scopeLabel?: string;
    onUpload?: () => void;
    onRetry?: () => void;
}) {
    const isLoading = kind === "loading";
    const content = isLoading
        ? {
              title: "正在準備知識庫",
              description: "檢索空間正在初始化，完成後就能開始提問。",
          }
        : copy[kind];

    return (
        <Empty
            className="min-h-80 border border-dashed border-border/80 bg-card/40 px-6 py-12"
            aria-live="polite"
            role={kind === "error" ? "alert" : undefined}
        >
            <EmptyHeader>
                <EmptyMedia
                    variant="icon"
                    className={
                        kind === "error"
                            ? "bg-destructive/10 text-destructive"
                            : "bg-primary/10 text-primary"
                    }
                >
                    {isLoading ? (
                        <LoaderCircle className="animate-spin" aria-hidden="true" />
                    ) : kind === "error" ? (
                        <CircleAlert aria-hidden="true" />
                    ) : (
                        <LibraryBig aria-hidden="true" />
                    )}
                </EmptyMedia>
                <EmptyTitle className="text-base">{content.title}</EmptyTitle>
                <EmptyDescription>
                    {scopeLabel && kind === "category-empty"
                        ? `${scopeLabel}：${content.description}`
                        : content.description}
                </EmptyDescription>
            </EmptyHeader>
            <EmptyContent className="sm:flex-row sm:justify-center">
                {onUpload ? (
                    <Button type="button" onClick={onUpload} className="gap-2">
                        <Upload size={16} aria-hidden="true" />
                        {kind === "empty" ? "上傳第一份文件" : "前往知識庫上傳"}
                        <ArrowRight size={15} aria-hidden="true" />
                    </Button>
                ) : null}
                {onRetry && (kind === "error" || isLoading) ? (
                    <Button
                        type="button"
                        onClick={onRetry}
                        variant="outline"
                        className="gap-2"
                    >
                        <RefreshCw size={15} aria-hidden="true" />
                        重新檢查
                    </Button>
                ) : null}
            </EmptyContent>
        </Empty>
    );
}
