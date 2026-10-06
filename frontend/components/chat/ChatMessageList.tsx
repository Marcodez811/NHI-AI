"use client";

import { Fragment, useEffect, useRef } from "react";
import { Check, ChevronDown, CircleAlert, File as FileIcon, FileText, ImageIcon, Loader2 } from "lucide-react";
import { MessageResponse } from "../ai-elements/message";
import { Badge } from "../ui/badge";
import { chatAttachmentContentUrl, type ChatAttachment } from "../../lib/api/chat";
import type { ChatTurn, ToolStep } from "../../lib/hooks/useChatSession";

const TOOL_NAMES: Record<string, { action: string; unit: string }> = {
    search_knowledge_base: { action: "搜尋知識庫", unit: "次" },
    read_attachment: { action: "讀取附件", unit: "個" },
    open_attachment: { action: "重新開啟附件", unit: "個" },
};

function toolSummary(steps: ToolStep[]): string {
    const counts = new Map<string, number>();
    for (const step of steps) counts.set(step.tool, (counts.get(step.tool) ?? 0) + 1);
    return [...counts].map(([tool, count]) => {
        const { action, unit } = TOOL_NAMES[tool] ?? { action: "使用工具", unit: "次" };
        return `已${action} ${count} ${unit}`;
    }).join("・");
}

function ToolStepRow({ step }: { step: ToolStep }) {
    return (
        <div className="chat-arrival flex items-center gap-2 text-xs text-muted-foreground">
            {step.done ? (
                <Check size={13} className="shrink-0 text-primary" aria-hidden="true" />
            ) : (
                <Loader2 size={13} className="shrink-0 animate-spin" aria-hidden="true" />
            )}
            <span className={step.done ? "" : "chat-shimmer"}>{step.label}</span>
        </div>
    );
}

function ToolSteps({ message }: { message: ChatTurn }) {
    const steps = message.toolSteps ?? [];
    if (!steps.length) return null;
    if (message.pending && !message.answerStarted && !message.content) {
        return (
            <div className="mb-3 flex flex-col gap-1.5" aria-label="工具使用進度">
                {steps.map((step, index) => <ToolStepRow key={index} step={step} />)}
            </div>
        );
    }
    return (
        <details className="group mb-3 text-xs text-muted-foreground">
            <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-md py-1 hover:text-foreground [&::-webkit-details-marker]:hidden">
                <ChevronDown size={13} className="transition-transform group-open:rotate-180" aria-hidden="true" />
                {toolSummary(steps)}
            </summary>
            <div className="mt-1 flex flex-col gap-1.5 pl-5">
                {steps.map((step, index) => <ToolStepRow key={index} step={step} />)}
            </div>
        </details>
    );
}

function ReasoningBlock({ message }: { message: ChatTurn }) {
    const details = useRef<HTMLDetailsElement>(null);
    useEffect(() => {
        if (details.current) details.current.open = Boolean(message.reasoningStreaming);
    }, [message.reasoningStreaming]);
    if (!message.reasoning) return null;
    return (
        <details ref={details} className="group mb-3 text-xs leading-6 text-muted-foreground">
            <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 rounded-md py-1 hover:text-foreground [&::-webkit-details-marker]:hidden">
                <ChevronDown size={13} className="transition-transform group-open:rotate-180" aria-hidden="true" />
                {message.reasoningStreaming ? "思考過程" : message.reasoningDurationSeconds != null
                    ? `思考了 ${message.reasoningDurationSeconds} 秒` : "思考過程"}
            </summary>
            <div className="mt-1 break-words pl-5 [&_p]:my-1.5">
                <MessageResponse>{message.reasoning}</MessageResponse>
            </div>
        </details>
    );
}

/** Icon and tint by extension: PDF red, Word blue, anything else neutral. */
function sourceIcon(name: string): { Icon: typeof FileText; className: string } {
    const ext = name.split(".").pop()?.toLowerCase();
    if (ext === "pdf") return { Icon: FileText, className: "text-red-600 dark:text-red-400" };
    if (ext === "docx" || ext === "doc") return { Icon: FileText, className: "text-blue-600 dark:text-blue-400" };
    return { Icon: FileIcon, className: "text-muted-foreground" };
}

function SourceChips({ sources }: { sources: ChatTurn["sources"] }) {
    if (!sources?.length) return null;
    return (
        <section className="mt-4" aria-label="資料來源">
            <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                <span>資料來源</span>
                <span className="rounded-full bg-muted px-1.5 text-[11px] leading-4 tabular-nums">{sources.length}</span>
            </div>
            <div className="flex flex-wrap gap-2">
            {sources.map((source, index) => {
                const { Icon, className } = sourceIcon(source.name);
                return (
                    <div key={`${source.name}-${index}`} title={source.snippet ? `${source.name}\n\n${source.snippet}` : source.name}
                        className="chat-arrival flex min-w-0 max-w-full items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs sm:max-w-80">
                        <Icon size={14} className={`shrink-0 ${className}`} aria-hidden="true" />
                        <span className="truncate text-foreground">{source.name}</span>
                    </div>
                );
            })}
            </div>
        </section>
    );
}

/** Read-only chips for the files sent with a user message; unknown ids are skipped. */
function MessageAttachments({ ids, lookup, sessionId }: {
    ids: string[] | null | undefined;
    lookup: Record<string, ChatAttachment>;
    sessionId: string | null | undefined;
}) {
    const items = (ids ?? []).map((id) => lookup[id]).filter((item): item is ChatAttachment => Boolean(item));
    if (!items.length) return null;
    return (
        <div className="mb-1.5 flex flex-wrap justify-end gap-1.5" aria-label="附件">
            {items.map((item) => {
                const Icon = item.kind === "image" ? ImageIcon : FileText;
                const chip = (
                    <>
                        <Icon size={14} className="shrink-0 text-muted-foreground" aria-hidden="true" />
                        <span className="truncate font-medium text-foreground">{item.display_name}</span>
                        {item.source === "knowledge_base" && <Badge variant="secondary" className="shrink-0">知識庫</Badge>}
                    </>
                );
                const className = "flex max-w-64 items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs";
                return item.source !== "knowledge_base" && sessionId ? (
                    <a key={item.id} href={chatAttachmentContentUrl(sessionId, item.id)} target="_blank" rel="noreferrer"
                        className={`${className} hover:bg-accent`}>{chip}</a>
                ) : (
                    <div key={item.id} className={className}>{chip}</div>
                );
            })}
        </div>
    );
}

const NO_ATTACHMENTS: Record<string, ChatAttachment> = {};

export function ChatMessageList({ messages, compactedThroughMessageId, attachmentLookup = NO_ATTACHMENTS, sessionId }: {
    attachmentLookup?: Record<string, ChatAttachment>;
    sessionId?: string | null;
    messages: ChatTurn[];
    compactedThroughMessageId?: string | null;
}) {
    return (
        <div className="flex w-full min-w-0 flex-col gap-10">
            {messages.map((message) => (
                <Fragment key={message.localKey}>
                    <div className={`chat-arrival flex min-w-0 ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                        {message.role === "user" ? (
                            <div className="flex min-w-0 max-w-[85%] flex-col items-end sm:max-w-[70%]">
                                <MessageAttachments ids={message.attachment_ids} lookup={attachmentLookup} sessionId={sessionId} />
                                <div className="min-w-0 rounded-3xl bg-primary/10 px-4 py-2.5 text-base leading-relaxed text-foreground">
                                    <div className="whitespace-pre-wrap break-words">{message.content}</div>
                                </div>
                            </div>
                        ) : (
                            <div className="w-full min-w-0 text-base leading-relaxed text-foreground">
                                {message.compacting && (
                                    <div role="status" className="mb-3 text-xs text-muted-foreground chat-shimmer">整理先前對話中…</div>
                                )}
                                <ReasoningBlock message={message} />
                                <ToolSteps message={message} />
                                {message.content ? (
                                    <MessageResponse className={message.pending ? "chat-streaming-answer" : undefined} isAnimating={Boolean(message.pending)}>
                                        {message.content}
                                    </MessageResponse>
                                ) : message.pending && !message.compacting && !message.reasoning && !(message.toolSteps?.length) ? (
                                    <div role="status" aria-live="polite" className="py-1 text-muted-foreground chat-shimmer">
                                        思考中…
                                    </div>
                                ) : null}
                                <SourceChips sources={message.sources} />
                                {message.status === "interrupted" && (
                                    <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                                        <CircleAlert size={13} aria-hidden="true" />已中止回覆
                                    </div>
                                )}
                                {message.status === "error" && (
                                    <div role="alert" className="mt-2 flex items-center gap-1.5 text-xs text-destructive">
                                        <CircleAlert size={13} aria-hidden="true" />{message.errorText || "回覆時發生錯誤"}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                    {message.id === compactedThroughMessageId && (
                        <div className="flex items-center gap-3 text-xs text-muted-foreground" role="separator" aria-label="已摘要較早的對話">
                            <span className="h-px flex-1 bg-border" aria-hidden="true" />
                            已摘要較早的對話
                            <span className="h-px flex-1 bg-border" aria-hidden="true" />
                        </div>
                    )}
                </Fragment>
            ))}
        </div>
    );
}
