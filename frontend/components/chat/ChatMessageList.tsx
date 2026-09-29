"use client";

import { Fragment, useEffect, useRef } from "react";
import { Check, ChevronDown, CircleAlert, Loader2 } from "lucide-react";
import { MessageResponse } from "../ai-elements/message";
import { Badge } from "../ui/badge";
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

function SourceChips({ sources }: { sources: ChatTurn["sources"] }) {
    if (!sources?.length) return null;
    return (
        <div className="mt-3 flex flex-wrap gap-1.5">
            {sources.map((source, index) => (
                <Badge key={`${source.name}-${index}`} variant="outline" className="chat-arrival max-w-64 truncate" title={source.snippet}>
                    {source.name}
                </Badge>
            ))}
        </div>
    );
}

export function ChatMessageList({ messages, compactedThroughMessageId }: {
    messages: ChatTurn[];
    compactedThroughMessageId?: string | null;
}) {
    return (
        <div className="flex w-full min-w-0 flex-col gap-7">
            {messages.map((message) => (
                <Fragment key={message.localKey}>
                    <div className={`chat-arrival flex min-w-0 ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                        {message.role === "user" ? (
                            <div className="min-w-0 max-w-[85%] rounded-2xl bg-primary px-4 py-2.5 text-sm leading-7 text-primary-foreground sm:max-w-[72%]">
                                <div className="whitespace-pre-wrap break-words">{message.content}</div>
                            </div>
                        ) : (
                            <div className="w-full min-w-0 text-sm leading-7 text-foreground">
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
