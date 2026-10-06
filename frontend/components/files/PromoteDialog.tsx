"use client";

import { useState } from "react";
import { getApiErrorMessage } from "../../lib/api/client";
import { promoteUserFile } from "../../lib/api/files";
import type { UserFile } from "../../lib/api/files";
import type { Category } from "../../lib/api/documents";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { PromoteFields } from "./PromoteFields";

/** 「加入知識庫」 for one stored file (used by the 我的檔案 page). */
export function PromoteDialog({
    file,
    onClose,
    onDone,
}: {
    file: UserFile | null;
    onClose: () => void;
    onDone: () => void;
}) {
    const [category, setCategory] = useState<Category | null>(null);
    const [folderId, setFolderId] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const close = () => {
        setCategory(null);
        setFolderId(null);
        setError(null);
        onClose();
    };

    const submit = async () => {
        if (!file || !category) return;
        setBusy(true);
        setError(null);
        try {
            await promoteUserFile(file.id, { category, folder_id: folderId });
            setCategory(null);
            setFolderId(null);
            onDone();
        } catch (caught) {
            setError(getApiErrorMessage(caught));
        } finally {
            setBusy(false);
        }
    };

    return (
        <Dialog open={file !== null} onOpenChange={(open) => { if (!open) close(); }}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>加入知識庫</DialogTitle>
                    <DialogDescription className="break-all">{file?.display_name}</DialogDescription>
                </DialogHeader>
                <PromoteFields
                    category={category}
                    folderId={folderId}
                    onCategoryChange={setCategory}
                    onFolderChange={setFolderId}
                />
                {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
                <DialogFooter>
                    <Button type="button" variant="outline" onClick={close} disabled={busy}>取消</Button>
                    <Button type="button" onClick={() => void submit()} disabled={!category || busy}>確定</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
