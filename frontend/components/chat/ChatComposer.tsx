"use client";

import { useRef, useState } from "react";
import type { ClipboardEvent, DragEvent } from "react";
import { ArrowUp, Paperclip, Square } from "lucide-react";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";
import { ChatAttachmentChip } from "./ChatAttachmentChip";
import { ChatModelPicker } from "./ChatModelPicker";
import type { ChatModelOption } from "../../lib/api/chat";
import type { ComposerAttachment } from "../../lib/hooks/useChatSession";

const ACCEPTED_FILE_TYPES = ".pdf,.docx,.txt,.md,image/png,image/jpeg,image/webp";

export function ChatComposer({
    sessionId,
    draft,
    setDraft,
    attachments,
    onAttachFiles,
    onRemoveAttachment,
    models,
    model,
    setModel,
    modelsLoading,
    busy,
    uploadsPending,
    onSend,
    onStop,
}: {
    sessionId: string | null;
    draft: string;
    setDraft: (value: string) => void;
    attachments: ComposerAttachment[];
    onAttachFiles: (files: File[]) => void;
    onRemoveAttachment: (localId: string) => void;
    models: ChatModelOption[];
    model: string | undefined;
    setModel: (value: string) => void;
    modelsLoading: boolean;
    busy: boolean;
    uploadsPending: boolean;
    onSend: () => void;
    onStop: () => void;
}) {
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [dragActive, setDragActive] = useState(false);
    const sendBlocked = busy || uploadsPending || !draft.trim();

    const handleDrop = (event: DragEvent<HTMLDivElement>) => {
        event.preventDefault();
        setDragActive(false);
        const files = Array.from(event.dataTransfer.files || []);
        if (files.length) onAttachFiles(files);
    };

    const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
        const files = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"));
        if (files.length) onAttachFiles(files);
    };

    return (
        <div className="pb-7">
            {attachments.length > 0 && (
                <div className="mb-2 flex flex-wrap gap-2">
                    {attachments.map((item) => (
                        <ChatAttachmentChip
                            key={item.localId}
                            sessionId={sessionId}
                            item={item}
                            onRemove={() => onRemoveAttachment(item.localId)}
                        />
                    ))}
                </div>
            )}
            <div
                className={`flex flex-col gap-1.5 rounded-xl border p-2 shadow-sm transition-colors ${dragActive ? "border-primary bg-primary/5" : "border-border bg-card"}`}
                onDragOver={(event) => {
                    event.preventDefault();
                    setDragActive(true);
                }}
                onDragLeave={() => setDragActive(false)}
                onDrop={handleDrop}
            >
                <Textarea
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                        event.preventDefault();
                        onSend();
                    }}
                    onPaste={handlePaste}
                    placeholder="輸入訊息，Enter 送出、Shift + Enter 換行…"
                    rows={1}
                    className="min-h-12 max-h-64 flex-1 resize-none overflow-y-auto border-0 bg-transparent px-2 py-1 text-sm shadow-none focus-visible:ring-0"
                />
                <div className="flex items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-1.5">
                        <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            onClick={() => fileInputRef.current?.click()}
                            aria-label="附加檔案"
                        >
                            <Paperclip size={16} />
                        </Button>
                        <input
                            ref={fileInputRef}
                            type="file"
                            multiple
                            accept={ACCEPTED_FILE_TYPES}
                            className="hidden"
                            onChange={(event) => {
                                const files = Array.from(event.target.files || []);
                                if (files.length) onAttachFiles(files);
                                event.target.value = "";
                            }}
                        />
                        <ChatModelPicker models={models} value={model} onValueChange={setModel} disabled={modelsLoading} />
                    </div>
                    <Button
                        type="button"
                        onClick={busy ? onStop : onSend}
                        disabled={!busy && sendBlocked}
                        variant="default"
                        size="icon"
                        aria-label={busy ? "停止" : "送出"}
                    >
                        {busy ? <Square size={14} /> : <ArrowUp size={16} />}
                    </Button>
                </div>
            </div>
            <p className="mt-2 text-center text-xs text-muted-foreground">
                附件僅供此對話使用，不會加入知識庫。
            </p>
        </div>
    );
}
