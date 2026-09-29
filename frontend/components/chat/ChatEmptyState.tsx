"use client";

import type { ReactNode } from "react";
import { Button } from "../ui/button";

const SUGGESTIONS = [
    { label: "最新健保給付政策重點", prompt: "幫我彙整最新健保給付政策的重點" },
    { label: "說明上傳文件的關鍵條款", prompt: "說明我上傳文件中的關鍵條款" },
    { label: "藥價調整相關討論", prompt: "搜尋知識庫中關於藥價調整的討論" },
] as const;

/**
 * Greeting, composer slot, and suggestion pills shown before the first
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
            <div className="mt-4 flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((item) => (
                    <Button
                        type="button"
                        key={item.label}
                        onClick={() => onPick(item.prompt)}
                        variant="outline"
                        size="sm"
                        className="rounded-full text-muted-foreground"
                    >
                        {item.label}
                    </Button>
                ))}
            </div>
        </div>
    );
}
