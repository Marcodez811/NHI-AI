"use client";

import { useMemo } from "react";
import type { ComponentProps } from "react";

import type { Category, FolderRead, QaModeInfo } from "../../lib/api/documents";
import { CATEGORY_LABELS } from "../../lib/api/documents";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "../ui/select";

const ALL_CATEGORIES = "all";
const ALL_FOLDERS = "_all";
const UNCATEGORIZED = "_uncategorized";

type TriggerProps = Omit<
    ComponentProps<typeof SelectTrigger>,
    "children" | "value" | "defaultValue" | "onValueChange"
>;

export function CategorySelect({
    modes,
    value,
    onValueChange,
    includeAll = false,
    ...triggerProps
}: {
    modes: QaModeInfo[];
    value: Category | typeof ALL_CATEGORIES;
    onValueChange: (value: Category | typeof ALL_CATEGORIES) => void;
    includeAll?: boolean;
} & TriggerProps) {
    const items = useMemo<Array<{ value: string; label: string }>>(
        () => [
            ...(includeAll ? [{ value: ALL_CATEGORIES, label: "全部分類" }] : []),
            ...modes.map((mode) => ({ value: mode.mode, label: mode.label })),
        ],
        [includeAll, modes],
    );
    const selectedLabel = value === ALL_CATEGORIES
        ? "全部分類"
        : modes.find((mode) => mode.mode === value)?.label ?? CATEGORY_LABELS[value as Category];

    return (
        <Select
            items={items}
            value={value}
            onValueChange={(nextValue) => {
                if (nextValue) onValueChange(nextValue as Category | typeof ALL_CATEGORIES);
            }}
        >
            <SelectTrigger {...triggerProps}>
                <SelectValue>{selectedLabel}</SelectValue>
            </SelectTrigger>
            <SelectContent>
                {includeAll && <SelectItem value={ALL_CATEGORIES}>全部分類</SelectItem>}
                {modes.map((mode) => (
                    <SelectItem key={mode.mode} value={mode.mode}>{mode.label}</SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}

export function FolderSelect({
    folders,
    value,
    onValueChange,
    includeAll = false,
    ...triggerProps
}: {
    folders: FolderRead[];
    value: string;
    onValueChange: (value: string) => void;
    includeAll?: boolean;
} & TriggerProps) {
    const items = useMemo<Array<{ value: string; label: string }>>(
        () => [
            ...(includeAll ? [{ value: ALL_FOLDERS, label: "全部資料夾" }] : []),
            { value: UNCATEGORIZED, label: "未分類" },
            ...folders.map((folder) => ({ value: folder.id, label: folder.name })),
        ],
        [folders, includeAll],
    );
    const selectedLabel = value === ALL_FOLDERS
        ? "全部資料夾"
        : value === UNCATEGORIZED
          ? "未分類"
          : folders.find((folder) => folder.id === value)?.name ?? "未分類";

    return (
        <Select
            items={items}
            value={value}
            onValueChange={(nextValue) => {
                if (nextValue) onValueChange(nextValue);
            }}
        >
            <SelectTrigger {...triggerProps}>
                <SelectValue>{selectedLabel}</SelectValue>
            </SelectTrigger>
            <SelectContent>
                {includeAll && <SelectItem value={ALL_FOLDERS}>全部資料夾</SelectItem>}
                <SelectItem value={UNCATEGORIZED}>未分類</SelectItem>
                {folders.map((folder) => (
                    <SelectItem key={folder.id} value={folder.id}>{folder.name}</SelectItem>
                ))}
            </SelectContent>
        </Select>
    );
}
