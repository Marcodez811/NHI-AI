"use client";

import { CircleAlert, LoaderCircle } from "lucide-react";
import { useChatEngine } from "../../lib/hooks/useChatEngine";
import { ChatComposer } from "./ChatComposer";
import { ChatEmptyState } from "./ChatEmptyState";
import { ChatMessageList } from "./ChatMessageList";

/**
 * The chat surface for both `/chat` (no session yet) and `/chat/[sessionId]`.
 * State lives one level up in `ChatEngineProvider` (see that file for why);
 * this component is a thin, route-agnostic view over it.
 */
export function ChatPage() {
    const chat = useChatEngine();

    const showEmptyState = !chat.sessionId && chat.messages.length === 0 && !chat.loading;

    return (
        <section className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-3xl flex-col px-5">
            <div className={`flex flex-1 flex-col ${chat.messages.length ? "justify-start py-8" : "justify-center py-12"}`}>
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
                ) : showEmptyState ? (
                    <ChatEmptyState onPick={chat.setDraft} />
                ) : (
                    <ChatMessageList messages={chat.messages} />
                )}
            </div>
            {!chat.loadError && (
                <>
                    {chat.sendError && (
                        <div role="alert" className="mb-2 flex items-center gap-2 text-xs text-red-600">
                            <CircleAlert size={14} />
                            {chat.sendError}
                        </div>
                    )}
                    <ChatComposer
                        sessionId={chat.sessionId}
                        draft={chat.draft}
                        setDraft={chat.setDraft}
                        attachments={chat.attachments}
                        onAttachFiles={(files) => void chat.attachFiles(files)}
                        onRemoveAttachment={chat.removeAttachment}
                        models={chat.models}
                        model={chat.model}
                        setModel={chat.setModel}
                        modelsLoading={chat.modelsLoading}
                        busy={chat.busy}
                        uploadsPending={chat.uploadsPending}
                        onSend={() => void chat.send()}
                        onStop={chat.stop}
                    />
                </>
            )}
        </section>
    );
}
