"use client";

import { useRef, useState } from "react";
import type { ClipboardEvent, DragEvent } from "react";
import { ArrowUp, FolderOpen, Library, Paperclip, Plus, Square, Zap } from "lucide-react";
import { Button } from "../ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import { promoteUserFile } from "../../lib/api/files";
import { AttachPickerDialog } from "./AttachPickerDialog";
import type { AttachEntry, PickerSource } from "./AttachPickerDialog";
import { UploadDestinationDialog } from "./UploadDestinationDialog";
import type { UploadChoice } from "./UploadDestinationDialog";
import { Textarea } from "../ui/textarea";
import { ChatAttachmentChip } from "./ChatAttachmentChip";
import { ChatModelPicker } from "./ChatModelPicker";
import type { ChatAttachment, ChatModelOption } from "../../lib/api/chat";
import type { ComposerAttachment } from "../../lib/hooks/useChatSession";

const ACCEPTED_FILE_TYPES = ".pdf,.docx,.txt,.md,image/png,image/jpeg,image/webp";

export function ChatComposer({
    sessionId,
    draft,
    setDraft,
    attachments,
    onAttachFiles,
    onAttachExisting,
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
    /** Resolves with the uploads that succeeded (used to copy them into the knowledge base). */
    onAttachFiles: (files: File[]) => Promise<ChatAttachment[]> | void;
    onAttachExisting?: (source: PickerSource, entries: AttachEntry[]) => Promise<void>;
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
    const [picker, setPicker] = useState<PickerSource | null>(null);
    const [pendingUpload, setPendingUpload] = useState<Promise<ChatAttachment[]> | null>(null);
    const sendBlocked = busy || uploadsPending || !draft.trim();

    /** Picked or dropped files: upload now and ask where they should live. Pasted images skip the ask. */
    const attachPicked = (files: File[]) => {
        const upload = Promise.resolve(onAttachFiles(files)).then((done) => done ?? []);
        if (files.some((file) => !file.type.startsWith("image/"))) setPendingUpload(upload);
    };

    const resolveUpload = async (choice: UploadChoice) => {
        const upload = pendingUpload;
        setPendingUpload(null);
        if (!upload || !choice.promote) return;
        const uploaded = await upload.catch(() => [] as ChatAttachment[]);
        await Promise.all(
            uploaded
                .filter((item) => item.kind !== "image")
                .map((item) => promoteUserFile(item.id, { category: choice.category, folder_id: choice.folderId }).catch(() => undefined)),
        );
    };

    const handleDrop = (event: DragEvent<HTMLDivElement>) => {
        event.preventDefault();
        setDragActive(false);
        const files = Array.from(event.dataTransfer.files || []);
        if (files.length) attachPicked(files);
    };

    const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
        const files = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"));
        if (files.length) void onAttachFiles(files);
    };

    return (
        <div
            data-slot="chat-composer"
            className={`flex flex-col gap-2 rounded-3xl border bg-card px-3 pb-2.5 pt-3 shadow-sm transition-colors focus-within:border-ring/50 ${dragActive ? "border-primary bg-primary/5" : "border-border/60"}`}
            onDragOver={(event) => {
                event.preventDefault();
                setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={handleDrop}
        >
            {attachments.length > 0 && (
                <div className="flex flex-wrap gap-2">
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
            <Textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                    event.preventDefault();
                    onSend();
                }}
                onPaste={handlePaste}
                placeholder="問問健保署 AI…"
                rows={1}
                // The outer container owns the border and focus ring. Clear every
                // textarea style that would draw a second box inside it,
                // including the global focus ring offset from globals.css.
                className="min-h-[1.75rem] max-h-64 w-full resize-none overflow-y-auto rounded-none border-0 bg-transparent px-1.5 py-0 text-base leading-7 shadow-none outline-none focus-visible:border-0 focus-visible:ring-0 focus-visible:ring-offset-0 dark:bg-transparent"
            />
            <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-1">
                    <DropdownMenu>
                        <DropdownMenuTrigger
                            aria-label="附加檔案"
                            className="inline-flex size-8 items-center justify-center rounded-lg text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring data-popup-open:bg-muted data-popup-open:text-foreground"
                        >
                            <Plus size={16} />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" side="top" sideOffset={8} className="w-56">
                            <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>
                                <Paperclip /> 上傳檔案
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => setPicker("knowledge_base")}>
                                <Library /> 從知識庫加入
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => setPicker("upload")}>
                                <FolderOpen /> 從我的檔案加入
                            </DropdownMenuItem>
                            <DropdownMenuItem disabled>
                                <Zap /> 使用技能
                                <span className="ml-auto text-xs text-muted-foreground">即將推出</span>
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                    <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept={ACCEPTED_FILE_TYPES}
                        className="hidden"
                        onChange={(event) => {
                            const files = Array.from(event.target.files || []);
                            if (files.length) attachPicked(files);
                            event.target.value = "";
                        }}
                    />
                </div>
                <div data-slot="chat-composer-send-group" className="flex shrink-0 items-center gap-1.5">
                    <ChatModelPicker models={models} value={model} onValueChange={setModel} disabled={modelsLoading} />
                    <Button
                        type="button"
                        onClick={busy ? onStop : onSend}
                        disabled={!busy && sendBlocked}
                        variant="default"
                        size="icon"
                        className="rounded-full"
                        aria-label={busy ? "停止" : "送出"}
                    >
                        {busy ? <Square size={14} /> : <ArrowUp size={16} />}
                    </Button>
                </div>
            </div>
            <AttachPickerDialog
                source={picker}
                onClose={() => setPicker(null)}
                onConfirm={async (source, entries) => { await onAttachExisting?.(source, entries); }}
            />
            <UploadDestinationDialog open={pendingUpload !== null} onConfirm={(choice) => void resolveUpload(choice)} />
        </div>
    );
}
