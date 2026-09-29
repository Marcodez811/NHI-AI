"use client";

import { ChevronDown } from "lucide-react";
import type { ChatModelOption } from "../../lib/api/chat";
import { ProviderIcon } from "./ProviderIcon";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuLabel,
    DropdownMenuRadioGroup,
    DropdownMenuRadioItem,
    DropdownMenuTrigger,
} from "../ui/dropdown-menu";

/** Model picker for the chat box; unavailable models stay visible but disabled. */
export function ChatModelPicker({
    models,
    value,
    onValueChange,
    disabled = false,
}: {
    models: ChatModelOption[];
    value: string | undefined;
    onValueChange: (value: string) => void;
    disabled?: boolean;
}) {
    const selected = models.find((model) => model.id === value);
    return (
        <DropdownMenu>
            <DropdownMenuTrigger
                aria-label="選擇模型"
                disabled={disabled || !models.length}
                className="inline-flex h-8 max-w-[12rem] items-center gap-1.5 rounded-lg px-2 text-xs text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 data-popup-open:bg-muted data-popup-open:text-foreground dark:hover:bg-muted/50"
            >
                {selected && <ProviderIcon provider={selected.provider} disabled={!selected.available} />}
                <span className="truncate">{selected?.label ?? "選擇模型"}</span>
                <ChevronDown className="size-3.5 shrink-0 opacity-60" aria-hidden="true" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" side="top" sideOffset={8} className="w-60">
                <DropdownMenuRadioGroup
                    value={value ?? ""}
                    onValueChange={(next) => {
                        if (next) onValueChange(String(next));
                    }}
                >
                    <DropdownMenuLabel>選擇模型</DropdownMenuLabel>
                    {models.map((model) => (
                        <DropdownMenuRadioItem
                            key={model.id}
                            value={model.id}
                            disabled={!model.available}
                            className="gap-2"
                        >
                            <ProviderIcon provider={model.provider} disabled={!model.available} />
                            <span className="truncate">{model.label}</span>
                            {!model.available && (
                                <span className="ml-auto pr-5 text-xs text-muted-foreground">未設定金鑰</span>
                            )}
                        </DropdownMenuRadioItem>
                    ))}
                </DropdownMenuRadioGroup>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
