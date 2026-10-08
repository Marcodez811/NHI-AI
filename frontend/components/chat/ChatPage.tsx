"use client";

import { useState } from "react";
import { ArrowDown, CircleAlert, LoaderCircle } from "lucide-react";
import { useChatEngine } from "../../lib/hooks/useChatEngine";
import { useStickToBottom } from "../../lib/hooks/useStickToBottom";
import { ChatComposer } from "./ChatComposer";
import { ChatEmptyState } from "./ChatEmptyState";
import { ChatMessageList } from "./ChatMessageList";
import { QaStepper } from "./qa/QaStepper";
import { ApiError } from "../../lib/api/client";

/**
 * The chat surface for both `/chat` (no session yet) and `/chat/[sessionId]`.
 * State lives one level up in `ChatEngineProvider` (see that file for why);
 * this component is a thin, route-agnostic view over it.
 */
export function ChatPage() {
    const chat = useChatEngine();
    const qa = chat.qa;
    const [skillError, setSkillError] = useState<string | null>(null);
    const skillAction = (action: () => Promise<void>) => async () => {
        setSkillError(null);
        try {
            await action();
        } catch (caught) {
            setSkillError(caught instanceof ApiError ? caught.message : "技能切換失敗，請稍後再試。");
        }
    };
    let latestUserKey: string | null = null;
    for (let index = chat.messages.length - 1; index >= 0; index--) {
        if (chat.messages[index].role === "user") {
            latestUserKey = chat.messages[index].localKey;
            break;
        }
    }
    const { contentRef, showLatest, scrollToBottom } = useStickToBottom({
        sessionId: chat.sessionId,
        loading: chat.loading,
        latestUserKey,
    });

    const showEmptyState = !chat.sessionId && chat.messages.length === 0 && !chat.loading;

    const composer = (
        <>
            {(chat.sendError || skillError) && (
                <div role="alert" className="mb-2 flex items-center gap-2 text-xs text-red-600">
                    <CircleAlert size={14} />
                    {chat.sendError ?? skillError}
                </div>
            )}
            <ChatComposer
                sessionId={chat.sessionId}
                draft={chat.draft}
                setDraft={chat.setDraft}
                attachments={chat.attachments}
                onAttachFiles={chat.attachFiles}
                onAttachExisting={chat.attachExisting}
                onRemoveAttachment={chat.removeAttachment}
                models={chat.models}
                model={chat.model}
                setModel={chat.setModel}
                modelsLoading={chat.modelsLoading}
                busy={chat.busy}
                uploadsPending={chat.uploadsPending}
                onSend={() => void chat.send()}
                onStop={chat.stop}
                skill={qa?.skill ?? null}
                onSelectSkill={qa ? skillAction(qa.enable) : undefined}
                onExitSkill={qa ? skillAction(qa.disable) : undefined}
            />
        </>
    );

    return (
        <section ref={contentRef} className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-3xl flex-col px-4 sm:px-5">
            {showEmptyState ? (
                <div data-chat-layout="centered" className="flex flex-1 flex-col items-center justify-center py-12">
                    <ChatEmptyState onPick={chat.setDraft}>{composer}</ChatEmptyState>
                </div>
            ) : (
                <>
                    <div className="flex flex-1 flex-col justify-start pt-12 pb-8 sm:pt-16">
                        {chat.loading ? (
                            <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
                                <LoaderCircle size={16} className="animate-spin" />
                                正在載入對話…
                            </div>
                        ) : chat.loadError ? (
                            <div role="alert" className="flex items-center justify-center gap-2 py-16 text-sm text-destructive">
                                <CircleAlert size={16} />
                                {chat.loadError}
                            </div>
                        ) : (
                            <>
                                {qa?.skill && <QaStepper stage={qa.workspace?.stage ?? "questions"} />}
                                <ChatMessageList messages={chat.messages} sessionId={chat.sessionId} attachmentLookup={chat.attachmentLookup} compactedThroughMessageId={chat.compactedThroughMessageId}
                                    qa={qa} onUploadFiles={chat.attachFiles} />
                            </>
                        )}
                    </div>
                    {!chat.loadError && (
                        <div
                            data-chat-layout="docked"
                            className="sticky bottom-0 z-10 bg-gradient-to-t from-background via-background/95 to-transparent pt-6 pb-7"
                        >
                            {composer}
                        </div>
                    )}
                </>
            )}
            {showLatest && chat.messages.length > 0 && (
                <button
                    type="button"
                    onClick={() => scrollToBottom(true)}
                    className="fixed bottom-24 left-1/2 z-10 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-border bg-card px-4 py-2 text-xs font-medium text-foreground shadow-md hover:bg-accent md:bottom-6"
                >
                    <ArrowDown size={14} aria-hidden="true" />
                    最新訊息
                </button>
            )}
        </section>
    );
}
