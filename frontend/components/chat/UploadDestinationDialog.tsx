"use client";

import { useState } from "react";
import type { Category } from "../../lib/api/documents";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { PromoteFields } from "../files/PromoteFields";

export type UploadChoice = { promote: false } | { promote: true; category: Category; folderId: string | null };

/** Asked once per uploaded batch; 「只用於此對話」 is the default. */
export function UploadDestinationDialog({
    open,
    onConfirm,
}: {
    open: boolean;
    onConfirm: (choice: UploadChoice) => void;
}) {
    const [promote, setPromote] = useState(false);
    const [category, setCategory] = useState<Category | null>(null);
    const [folderId, setFolderId] = useState<string | null>(null);

    const reset = () => {
        setPromote(false);
        setCategory(null);
        setFolderId(null);
    };
    const confirm = () => {
        const choice: UploadChoice = promote && category
            ? { promote: true, category, folderId }
            : { promote: false };
        reset();
        onConfirm(choice);
    };
    const dismiss = () => {
        reset();
        onConfirm({ promote: false });
    };

    return (
        <Dialog open={open} onOpenChange={(next) => { if (!next) dismiss(); }}>
            <DialogContent showCloseButton={false}>
                <DialogHeader>
                    <DialogTitle>要將這些檔案加入知識庫嗎？</DialogTitle>
                    <DialogDescription>檔案已可在此對話中使用。</DialogDescription>
                </DialogHeader>
                <div className="grid gap-2" role="radiogroup" aria-label="檔案用途">
                    <label className="flex items-center gap-2 text-sm">
                        <input type="radio" name="upload-destination" checked={!promote} onChange={() => setPromote(false)} />
                        只用於此對話
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                        <input type="radio" name="upload-destination" checked={promote} onChange={() => setPromote(true)} />
                        同時加入知識庫
                    </label>
                </div>
                {promote && (
                    <PromoteFields
                        category={category}
                        folderId={folderId}
                        onCategoryChange={setCategory}
                        onFolderChange={setFolderId}
                    />
                )}
                <DialogFooter>
                    <Button type="button" onClick={confirm} disabled={promote && !category}>確定</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
