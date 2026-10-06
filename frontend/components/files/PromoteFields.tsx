"use client";

import { useEffect, useState } from "react";
import { CATEGORY_LABELS, CATEGORY_VALUES, fetchFolders } from "../../lib/api/documents";
import type { Category, FolderRead } from "../../lib/api/documents";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";

const NO_FOLDER = "_none";

export const PROMOTE_WARNING = "加入後所有人都能搜尋到，並需數分鐘建立索引。";

/** Category (required) and folder (optional) selects shared by every "add to knowledge base" flow. */
export function PromoteFields({
    category,
    folderId,
    onCategoryChange,
    onFolderChange,
}: {
    category: Category | null;
    folderId: string | null;
    onCategoryChange: (value: Category) => void;
    onFolderChange: (value: string | null) => void;
}) {
    const [folders, setFolders] = useState<FolderRead[]>([]);
    useEffect(() => {
        let cancelled = false;
        fetchFolders()
            .then((items) => { if (!cancelled) setFolders(items); })
            .catch(() => undefined);
        return () => { cancelled = true; };
    }, []);

    const categoryItems = CATEGORY_VALUES.map((value) => ({ value, label: CATEGORY_LABELS[value] }));
    const folderItems = [
        { value: NO_FOLDER, label: "不指定資料夾" },
        ...folders.map((folder) => ({ value: folder.id, label: folder.name })),
    ];

    return (
        <div className="grid gap-3">
            <div className="grid gap-1.5">
                <span className="text-xs font-medium text-muted-foreground">分類（必填）</span>
                <Select
                    items={categoryItems}
                    value={category}
                    onValueChange={(next) => { if (next) onCategoryChange(next as Category); }}
                >
                    <SelectTrigger aria-label="分類" className="w-full">
                        <SelectValue placeholder="選擇分類">
                            {category ? CATEGORY_LABELS[category] : "選擇分類"}
                        </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                        {categoryItems.map((item) => (
                            <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
            <div className="grid gap-1.5">
                <span className="text-xs font-medium text-muted-foreground">資料夾（選填）</span>
                <Select
                    items={folderItems}
                    value={folderId ?? NO_FOLDER}
                    onValueChange={(next) => { if (next) onFolderChange(next === NO_FOLDER ? null : String(next)); }}
                >
                    <SelectTrigger aria-label="資料夾" className="w-full">
                        <SelectValue>
                            {folderItems.find((item) => item.value === (folderId ?? NO_FOLDER))?.label ?? "不指定資料夾"}
                        </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                        {folderItems.map((item) => (
                            <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
            <p className="text-xs text-muted-foreground">{PROMOTE_WARNING}</p>
        </div>
    );
}
