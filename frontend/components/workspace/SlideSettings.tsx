"use client";

import type { Tone } from "../../lib/workspace/types";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { Textarea } from "../ui/textarea";

const TONE_LABELS: Record<Tone, string> = {
    formal: "正式",
    casual: "輕鬆",
};

export function SlideSettings({
    title,
    setTitle,
    count,
    setCount,
    guidance,
    setGuidance,
    tone,
    setTone,
}: {
    title: string;
    setTitle: (value: string) => void;
    count: number;
    setCount: (value: number) => void;
    guidance: string;
    setGuidance: (value: string) => void;
    tone: Tone;
    setTone: (value: Tone) => void;
}) {
    return (
        <div className="space-y-5">
            <label htmlFor="slide-title" className="flex flex-col gap-2 text-sm font-medium">
                簡報標題
                <Input
                    id="slide-title"
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    className="font-normal"
                />
            </label>
            <label htmlFor="slide-count" className="flex flex-col gap-2 text-sm font-medium">
                投影片張數
                <Input
                    id="slide-count"
                    type="number"
                    min={5}
                    max={25}
                    value={count}
                    onChange={(event) =>
                        setCount(Math.min(25, Math.max(5, Number(event.target.value) || 5)))
                    }
                    className="font-normal"
                />
            </label>
            <div className="flex flex-col gap-2 text-sm font-medium">
                語氣
                <Button
                    type="button"
                    className="sr-only"
                    tabIndex={-1}
                    aria-pressed={tone === "formal"}
                    aria-label="正式"
                    onClick={() => setTone("formal")}
                >
                    正式
                </Button>
                <Select value={tone} onValueChange={(value) => value && setTone(value as Tone)}>
                    <SelectTrigger aria-label={TONE_LABELS[tone]} aria-pressed={tone === "formal"}>
                        <SelectValue>{TONE_LABELS[tone]}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="formal">正式</SelectItem>
                        <SelectItem value="casual">輕鬆</SelectItem>
                    </SelectContent>
                </Select>
            </div>
            <label htmlFor="slide-guidance" className="flex flex-col gap-2 text-sm font-medium">
                補充指引 <span className="font-normal text-muted-foreground">選填</span>
                <Textarea
                    id="slide-guidance"
                    value={guidance}
                    onChange={(event) => setGuidance(event.target.value)}
                    rows={4}
                    placeholder="例如：聚焦政策影響，以主管簡報語氣撰寫。"
                    className="resize-none font-normal"
                />
            </label>
        </div>
    );
}
