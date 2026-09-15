"use client";

import { FileText, Upload, X } from "lucide-react";
import { Button } from "../ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "../ui/dialog";
import type { Category, FolderRead, QaModeInfo } from "../../lib/api/documents";
import { CategorySelect, FolderSelect } from "./DocumentSelects";

export function UploadModal({
    pending,
    setPending,
    category,
    setCategory,
    folderId,
    setFolderId,
    folders,
    modes,
    uploading,
    upload,
    close,
}: {
    pending: File[];
    setPending: React.Dispatch<React.SetStateAction<File[]>>;
    category: Category;
    setCategory: (value: Category) => void;
    folderId: string | null;
    setFolderId: (value: string | null) => void;
    folders: FolderRead[];
    modes: QaModeInfo[];
    uploading: boolean;
    upload: () => Promise<void>;
    close: () => void;
}) {
    return (
        <Dialog open onOpenChange={(open) => !open && close()}>
            <DialogContent className="sm:max-w-lg">
                <DialogHeader>
                    <DialogTitle>上傳文件</DialogTitle>
                    <DialogDescription>加入知識庫後，文件會在背景完成索引。</DialogDescription>
                </DialogHeader>
                <div className="grid gap-4 sm:grid-cols-2">
                    <div className="grid gap-2 text-sm font-medium">
                        <span>文件分類</span>
                        <CategorySelect
                            modes={modes}
                            value={category}
                            onValueChange={(value) => setCategory(value as Category)}
                        />
                    </div>
                    <div className="grid gap-2 text-sm font-medium">
                        <span>資料夾</span>
                        <FolderSelect
                            folders={folders}
                            value={folderId || "_uncategorized"}
                            onValueChange={(value) => setFolderId(value === "_uncategorized" ? null : value)}
                        />
                    </div>
                </div>
                <label className="mt-4 flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/30 text-center text-sm text-muted-foreground">
                    <Upload size={22} />
                    <span>點擊選取一或多份文件</span>
                    <span className="text-xs">PDF、DOCX、Markdown、TXT</span>
                    <input
                        type="file"
                        multiple
                        accept=".pdf,.docx,.md,.markdown,.txt"
                        className="hidden"
                        onChange={(event) =>
                            setPending(Array.from(event.target.files || []))
                        }
                    />
                </label>
                {pending.length > 0 && (
                    <div className="mt-4 flex flex-col gap-2">
                        {pending.map((file) => (
                            <div
                                className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm"
                                key={`${file.name}-${file.size}`}
                            >
                                <span className="flex items-center gap-2">
                                    <FileText size={15} />
                                    {file.name}
                                </span>
                                <Button
                                    type="button"
                                    variant="ghost"
                                    size="icon-xs"
                                    onClick={() =>
                                        setPending((items) =>
                                            items.filter(
                                                (item) => item !== file,
                                            ),
                                        )
                                    }
                                    aria-label={`移除 ${file.name}`}
                                >
                                    <X size={14} />
                                </Button>
                            </div>
                        ))}
                    </div>
                )}
                <DialogFooter>
                    <Button type="button" variant="outline" onClick={close}>取消</Button>
                    <Button
                        type="button"
                        disabled={!pending.length || uploading}
                        onClick={() => void upload()}
                    >
                        {uploading ? "上傳中…" : "開始上傳"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
