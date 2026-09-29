"use client";

import type { ReactNode } from "react";
import { FileText, Pill, Sparkles } from "lucide-react";

const SUGGESTIONS = [
    { label: "最新健保給付政策重點", prompt: "幫我彙整最新健保給付政策的重點", icon: Sparkles },
    { label: "說明上傳文件的關鍵條款", prompt: "說明我上傳文件中的關鍵條款", icon: FileText },
    { label: "藥價調整相關討論", prompt: "搜尋知識庫中關於藥價調整的討論", icon: Pill },
] as const;

/**
 * Greeting, composer slot, and suggestion rows shown before the first
 * message of a new conversation. The composer is passed in as `children` so
 * the caller (`ChatPage`) can keep a single `ChatComposer` instance that
 * moves between this centered layout and the docked one.
 */
export function ChatEmptyState({ onPick, children }: { onPick: (text: string) => void; children: ReactNode }) {
    return (
        <div className="mx-auto flex w-full max-w-2xl flex-col items-center text-center">
            <h1 className="text-2xl font-semibold tracking-tight">健保署 AI 助理</h1>
            <p className="mt-2 text-sm text-muted-foreground">
                提出問題或上傳文件，助理會搜尋知識庫並附上引用來源
            </p>
            <div className="mt-8 w-full">{children}</div>
            <div className="mt-3 flex w-full flex-col items-stretch">
                {SUGGESTIONS.map((item) => (
                    <button
                        type="button"
                        key={item.label}
                        onClick={() => onPick(item.prompt)}
                        className="flex items-center gap-2.5 rounded-lg px-2 py-2 text-left text-sm text-muted-foreground transition-colors hover:text-foreground"
                    >
                        <item.icon size={15} className="shrink-0 text-muted-foreground" aria-hidden="true" />
                        {item.label}
                    </button>
                ))}
            </div>
        </div>
    );
}
