"use client";

import { CircleAlert, FileText, Loader2, X } from "lucide-react";
import { Button } from "../ui/button";
import { chatAttachmentContentUrl } from "../../lib/api/chat";
import type { ComposerAttachment } from "../../lib/hooks/useChatSession";

/** One attachment chip: thumbnail (via the content URL once ready), status, remove. */
export function ChatAttachmentChip({
    sessionId,
    item,
    onRemove,
}: {
    sessionId: string | null;
    item: ComposerAttachment;
    onRemove: () => void;
}) {
    const isImage = (item.attachment?.kind ?? (item.file.type.startsWith("image/") ? "image" : "document")) === "image";
    const thumbnailUrl = item.status === "ready" && isImage && item.attachment && sessionId
        ? chatAttachmentContentUrl(sessionId, item.attachment.id)
        : null;

    return (
        <div className="flex max-w-64 items-center gap-2 rounded-lg border border-border bg-card px-2 py-1.5 text-xs">
            <div className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-md bg-muted">
                {thumbnailUrl ? (
                    // A server-processed preview (resized, metadata stripped), not a local blob.
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={thumbnailUrl} alt="" className="size-full object-cover" />
                ) : (
                    <FileText size={15} className="text-muted-foreground" />
                )}
            </div>
            <div className="min-w-0 flex-1">
                <div className="truncate font-medium text-foreground">{item.file.name}</div>
                <div className="flex items-center gap-1 text-muted-foreground">
                    {item.status === "uploading" && (
                        <>
                            <Loader2 size={11} className="animate-spin" />
                            上傳中…
                        </>
                    )}
                    {item.status === "ready" && (
                        // `error` can carry a non-fatal note even when ready, e.g. a PDF with no text layer.
                        <span className={item.error ? "truncate text-amber-600" : undefined}>
                            {item.error || "已就緒"}
                        </span>
                    )}
                    {item.status === "failed" && (
                        <>
                            <CircleAlert size={11} className="text-destructive" />
                            <span className="truncate text-destructive">{item.error || "上傳失敗"}</span>
                        </>
                    )}
                </div>
            </div>
            <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                onClick={onRemove}
                aria-label={`移除 ${item.file.name}`}
            >
                <X size={13} />
            </Button>
        </div>
    );
}
