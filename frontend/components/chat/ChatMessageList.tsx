"use client";

import { Check, CircleAlert, Loader2 } from "lucide-react";
import { MessageResponse } from "../ai-elements/message";
import { Badge } from "../ui/badge";
import type { ChatTurn, ToolStep } from "../../lib/hooks/useChatSession";

function ToolStepRow({ step }: { step: ToolStep }) {
    return (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {step.done ? (
                <Check size={13} className="shrink-0 text-primary" />
            ) : (
                <Loader2 size={13} className="shrink-0 animate-spin" />
            )}
            <span>{step.label}</span>
        </div>
    );
}

function SourceChips({ sources }: { sources: ChatTurn["sources"] }) {
    if (!sources?.length) return null;
    return (
        <div className="mt-3 flex flex-wrap gap-1.5">
            {sources.map((source, index) => (
                <Badge
                    key={`${source.name}-${index}`}
                    variant="outline"
                    className="max-w-64 truncate"
                    title={source.snippet}
                >
                    {source.name}
                </Badge>
            ))}
        </div>
    );
}

export function ChatMessageList({ messages }: { messages: ChatTurn[] }) {
    return (
        <div className="flex w-full min-w-0 flex-col gap-7">
            {messages.map((message) => (
                <div
                    key={message.localKey}
                    className={`flex min-w-0 ${message.role === "user" ? "justify-end" : "justify-start"}`}
                >
                    {message.role === "user" ? (
                        <div className="min-w-0 max-w-[85%] rounded-2xl bg-primary px-4 py-2.5 text-sm leading-7 text-primary-foreground sm:max-w-[72%]">
                            <div className="whitespace-pre-wrap break-words">{message.content}</div>
                        </div>
                    ) : (
                        <div className="w-full min-w-0 text-sm leading-7 text-foreground">
                            {(message.toolSteps ?? []).length > 0 && (
                                <div className="mb-3 flex flex-col gap-1.5">
                                    {(message.toolSteps ?? []).map((step, index) => (
                                        // eslint-disable-next-line react/no-array-index-key
                                        <ToolStepRow key={index} step={step} />
                                    ))}
                                </div>
                            )}
                            {message.content ? (
                                <MessageResponse
                                    animated={{ animation: "fadeIn", duration: 150, sep: "word" }}
                                    isAnimating={Boolean(message.pending)}
                                >
                                    {message.content}
                                </MessageResponse>
                            ) : message.pending ? (
                                <div role="status" aria-live="polite" className="py-1 text-muted-foreground">
                                    正在處理問題…
                                </div>
                            ) : null}
                            <SourceChips sources={message.sources} />
                            {message.status === "interrupted" && (
                                <div className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                                    <CircleAlert size={13} />
                                    已中止回覆
                                </div>
                            )}
                            {message.status === "error" && (
                                <div role="alert" className="mt-2 flex items-center gap-1.5 text-xs text-destructive">
                                    <CircleAlert size={13} />
                                    {message.errorText || "回覆時發生錯誤"}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            ))}
        </div>
    );
}
