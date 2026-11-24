"use client";

import { useState } from "react";
import { ArrowDown, CircleAlert, LoaderCircle } from "lucide-react";
import { useChatEngine } from "../../lib/hooks/useChatEngine";
import { useStickToBottom } from "../../lib/hooks/useStickToBottom";
import { ChatComposer } from "./ChatComposer";
import { ChatEmptyState } from "./ChatEmptyState";
import { ChatMessageList } from "./ChatMessageList";
import { QaPreviewPanel } from "./qa/QaPreviewPanel";
import type { QaController } from "./qa/QaCards";
import { QaStepper } from "./qa/QaStepper";
import type { ChatTurn } from "../../lib/hooks/useChatSession";
import { ApiError } from "../../lib/api/client";

function latestUserMessageKey(messages: ChatTurn[]): string | null {
    for (let index = messages.length - 1; index >= 0; index--) {
        if (messages[index].role === "user" && !messages[index].display_text) return messages[index].localKey;
    }
    return null;
}

/** The preview beside the chat (a sheet on phones) while a version is open. */
function PreviewDock({ sessionId, qa }: { sessionId: string | null; qa: QaController | undefined }) {
    if (!qa?.skill || !qa.previewId) return null;
    return (
        <QaPreviewPanel sessionId={sessionId} versions={qa.workspace?.versions ?? []} selectedId={qa.previewId}
            onSelect={qa.openPreview} onClose={qa.closePreview} generation={qa.generation} onRevise={qa.reviseVersion} />
    );
}

function ChatTranscript({ chat, qa }: { chat: ReturnType<typeof useChatEngine>; qa: QaController | undefined }) {
    if (chat.loading) {
        return (
            <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
                <LoaderCircle size={16} className="animate-spin" />
                正在載入對話…
            </div>
        );
    }
    if (chat.loadError) {
        return (
            <div role="alert" className="flex items-center justify-center gap-2 py-16 text-sm text-destructive">
                <CircleAlert size={16} />
                {chat.loadError}
            </div>
        );
    }
    return (
        <>
            {qa?.skill && <QaStepper stage={qa.workspace?.stage ?? "questions"} />}
            <ChatMessageList messages={chat.messages} sessionId={chat.sessionId} attachmentLookup={chat.attachmentLookup} compactedThroughMessageId={chat.compactedThroughMessageId}
                qa={qa} onUploadFiles={chat.attachFiles} />
        </>
    );
}

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
    const latestUserKey = latestUserMessageKey(chat.messages);
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
        <div className="flex items-start">
        <section ref={contentRef} className="mx-auto flex min-h-[calc(100vh-4rem)] w-full min-w-0 max-w-3xl flex-1 flex-col px-4 sm:px-5">
            {showEmptyState ? (
                <div data-chat-layout="centered" className="flex flex-1 flex-col items-center justify-center py-12">
                    <ChatEmptyState onPick={chat.setDraft}>{composer}</ChatEmptyState>
                </div>
            ) : (
                <>
                    <div className="flex flex-1 flex-col justify-start pt-12 pb-8 sm:pt-16">
                        <ChatTranscript chat={chat} qa={qa} />
                    </div>
                    {!chat.loadError && (
                        <div
                            data-chat-layout="docked"
                            className="sticky bottom-0 z-10 bg-gradient-to-t from-background via-background/95 to-transparent pt-6 pb-7"
                        >
                            {showLatest && chat.messages.length > 0 && (
                                <button
                                    type="button"
                                    onClick={() => scrollToBottom(true)}
                                    className="absolute bottom-full left-1/2 z-10 mb-1 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-border bg-card px-4 py-2 text-xs font-medium text-foreground shadow-md hover:bg-accent"
                                >
                                    <ArrowDown size={14} aria-hidden="true" />
                                    最新訊息
                                </button>
                            )}
                            {composer}
                        </div>
                    )}
                </>
            )}
        </section>
        <PreviewDock sessionId={chat.sessionId} qa={qa} />
        </div>
    );
}
