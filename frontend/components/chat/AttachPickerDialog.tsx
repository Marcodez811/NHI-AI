"use client";

import { useEffect, useState } from "react";
import { getApiErrorMessage } from "../../lib/api/client";
import { CATEGORY_LABELS, fetchDocuments } from "../../lib/api/documents";
import { fetchUserFiles } from "../../lib/api/files";
import type { ChatAttachment } from "../../lib/api/chat";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Input } from "../ui/input";

export type AttachEntry = Pick<ChatAttachment, "id" | "display_name" | "mime_type" | "kind" | "size_bytes">;
export type PickerSource = "knowledge_base" | "upload";

type Row = { entry: AttachEntry; detail: string };

const COPY: Record<PickerSource, { title: string; description: string; empty: string }> = {
    knowledge_base: { title: "從知識庫加入", description: "選擇要加入此對話的知識庫文件。", empty: "找不到可加入的文件" },
    upload: { title: "從我的檔案加入", description: "選擇先前上傳的檔案。", empty: "尚未上傳任何檔案" },
};

/** Multi-select list of knowledge-base documents or the user's stored files. */
export function AttachPickerDialog({
    source,
    onClose,
    onConfirm,
}: {
    source: PickerSource | null;
    onClose: () => void;
    onConfirm: (source: PickerSource, entries: AttachEntry[]) => Promise<void>;
}) {
    const [query, setQuery] = useState("");
    const [rows, setRows] = useState<Row[]>([]);
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!source) return;
        let cancelled = false;
        setLoading(true);
        setError(null);
        const load = async (): Promise<Row[]> => {
            if (source === "knowledge_base") {
                const docs = await fetchDocuments({ status: "ready", retrieval_enabled: true, q: query.trim() || undefined });
                return docs.map((doc) => ({
                    entry: {
                        id: doc.id,
                        display_name: doc.display_name || doc.original_filename,
                        mime_type: doc.mime_type,
                        kind: "document" as const,
                        size_bytes: doc.size_bytes,
                    },
                    detail: CATEGORY_LABELS[doc.category] ?? "",
                }));
            }
            const files = await fetchUserFiles();
            return files
                .filter((file) => file.status === "ready")
                .map((file) => ({
                    entry: {
                        id: file.id,
                        display_name: file.display_name,
                        mime_type: file.mime_type,
                        kind: file.kind,
                        size_bytes: file.size_bytes,
                    },
                    detail: file.origin_session_title ?? "",
                }));
        };
        const timer = setTimeout(() => {
            load()
                .then((next) => { if (!cancelled) setRows(next); })
                .catch((caught) => { if (!cancelled) setError(getApiErrorMessage(caught)); })
                .finally(() => { if (!cancelled) setLoading(false); });
        }, query ? 250 : 0);
        return () => { cancelled = true; clearTimeout(timer); };
    }, [source, query]);

    const close = () => {
        setQuery("");
        setRows([]);
        setSelected(new Set());
        setError(null);
        onClose();
    };

    const visible = source === "upload" && query.trim()
        ? rows.filter((row) => row.entry.display_name.toLowerCase().includes(query.trim().toLowerCase()))
        : rows;

    const submit = async () => {
        if (!source) return;
        setBusy(true);
        setError(null);
        try {
            await onConfirm(source, rows.filter((row) => selected.has(row.entry.id)).map((row) => row.entry));
            close();
        } catch (caught) {
            setError(getApiErrorMessage(caught));
        } finally {
            setBusy(false);
        }
    };

    const copy = COPY[source ?? "knowledge_base"];
    return (
        <Dialog open={source !== null} onOpenChange={(open) => { if (!open) close(); }}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>{copy.title}</DialogTitle>
                    <DialogDescription>{copy.description}</DialogDescription>
                </DialogHeader>
                <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜尋名稱" aria-label="搜尋" />
                <div className="max-h-72 overflow-y-auto rounded-md border border-border/60">
                    {loading && <p className="p-3 text-sm text-muted-foreground">載入中…</p>}
                    {!loading && visible.length === 0 && <p className="p-3 text-sm text-muted-foreground">{copy.empty}</p>}
                    {visible.map((row) => (
                        <label key={row.entry.id} className="flex cursor-pointer items-center gap-3 border-b border-border/40 px-3 py-2 last:border-b-0 hover:bg-muted/50">
                            <input
                                type="checkbox"
                                checked={selected.has(row.entry.id)}
                                onChange={(event) => setSelected((prev) => {
                                    const next = new Set(prev);
                                    if (event.target.checked) next.add(row.entry.id);
                                    else next.delete(row.entry.id);
                                    return next;
                                })}
                            />
                            <span className="min-w-0 flex-1">
                                <span className="block truncate text-sm">{row.entry.display_name}</span>
                                {row.detail && <span className="block truncate text-xs text-muted-foreground">{row.detail}</span>}
                            </span>
                        </label>
                    ))}
                </div>
                {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
                <DialogFooter>
                    <Button type="button" variant="outline" onClick={close} disabled={busy}>取消</Button>
                    <Button type="button" onClick={() => void submit()} disabled={selected.size === 0 || busy}>加入對話</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
