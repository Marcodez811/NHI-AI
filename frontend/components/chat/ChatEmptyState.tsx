"use client";

import { ChevronRight } from "lucide-react";
import { Button } from "../ui/button";

const EXAMPLE_PROMPTS = [
    "幫我彙整最新健保給付政策的重點",
    "說明我上傳文件中的關鍵條款",
    "搜尋知識庫中關於藥價調整的討論",
] as const;

/** Greeting shown before the first message of a new conversation. */
export function ChatEmptyState({ onPick }: { onPick: (text: string) => void }) {
    return (
        <div className="text-center">
            <h1 className="text-2xl font-semibold tracking-tight">健保署 AI 助理</h1>
            <p className="mt-2 text-sm text-muted-foreground">
                提出問題或上傳文件，助理會搜尋知識庫並附上引用來源
            </p>
            <div className="mx-auto mt-8 grid max-w-xl gap-2">
                {EXAMPLE_PROMPTS.map((text) => (
                    <Button
                        type="button"
                        key={text}
                        onClick={() => onPick(text)}
                        variant="outline"
                        className="h-auto justify-between p-3 text-left text-sm"
                    >
                        {text}
                        <ChevronRight size={15} />
                    </Button>
                ))}
            </div>
        </div>
    );
}
