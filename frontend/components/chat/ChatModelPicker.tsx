"use client";

import type { ChatModelOption } from "../../lib/api/chat";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "../ui/select";

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
        <Select
            items={models.map((model) => ({ value: model.id, label: model.label }))}
            value={value ?? ""}
            onValueChange={(next) => {
                if (next) onValueChange(next);
            }}
            disabled={disabled || !models.length}
        >
            <SelectTrigger size="sm" aria-label="選擇模型" className="max-w-[11rem] text-xs">
                <SelectValue>{selected?.label ?? "選擇模型"}</SelectValue>
            </SelectTrigger>
            <SelectContent>
                {models.map((model) => (
                    <SelectItem key={model.id} value={model.id} disabled={!model.available}>
                        {model.label}
                        {!model.available && (
                            <span className="text-muted-foreground">（未設定金鑰）</span>
                        )}
                    </SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}
