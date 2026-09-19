"use client";

import { useState } from "react";
import Link from "next/link";
import { FileText, Newspaper } from "lucide-react";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";
import { MessageResponse } from "../ai-elements/message";
import { MAX_DOCUMENTS, supportsSlideGeneration, type DocumentRead } from "../../lib/api";
import { useNewsJob } from "../../lib/hooks/useNewsJob";
import { documentDisplayName } from "./WorkspaceViewUtils";

const phaseLabels: Record<string, string> = {
    queued: "排隊中", preparing: "準備來源", extracting: "擷取來源內容",
    drafting: "撰寫新聞稿", publishing: "完成稿件", completed: "已完成", failed: "生成失敗",
};

export function NewsView({ docs, onBrowseSources }: { docs: DocumentRead[]; onBrowseSources: () => void }) {
    const [selected, setSelected] = useState<string[]>([]);
    const [guidance, setGuidance] = useState("");
    const [copied, setCopied] = useState(false);
    const job = useNewsJob();
    const available = docs.filter((doc) => doc.status === "ready" && supportsSlideGeneration(doc));
    const busy = job.phase === "submitting" || job.phase === "polling";
    const article = job.job?.status === "completed" ? job.job.article : null;

    const download = () => {
        if (!article) return;
        const url = URL.createObjectURL(new Blob([article], { type: "text/markdown;charset=utf-8" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = "健保新聞稿.md";
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    };

    return (
        <section className="px-5 py-8 lg:px-10 lg:py-10">
            <div className="mx-auto max-w-[720px]">
                <Link href="/workflows" className="mb-5 inline-flex items-center text-xs text-info hover:text-info/80">← 返回 AI 工作流</Link>
                <div className="mb-7 flex items-start gap-3">
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><Newspaper size={20} /></span>
                    <div><h1 className="text-[24px] font-semibold tracking-tight">生成新聞稿</h1><p className="mt-1 text-sm text-muted-foreground">從來源文件擷取事實，撰寫健保署風格的新聞稿。</p></div>
                </div>

                {article ? (
                    <div>
                        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                            <h2 className="font-semibold">新聞稿草稿</h2>
                            <div className="flex gap-2">
                                <Button variant="outline" onClick={async () => { await navigator.clipboard.writeText(article); setCopied(true); }}>{copied ? "已複製" : "複製全文"}</Button>
                                <Button variant="outline" onClick={download}>下載 Markdown</Button>
                            </div>
                        </div>
                        <article className="rounded-xl border border-border bg-card p-6 text-sm leading-8 sm:p-8"><MessageResponse>{article}</MessageResponse></article>
                        <Button className="mt-5" variant="outline" onClick={() => { job.reset(); setCopied(false); }}>生成新稿</Button>
                    </div>
                ) : (
                    <>
                        <div className="border-b border-border pb-7">
                            <div className="flex items-start justify-between gap-3"><div><h2 className="font-semibold">來源文件</h2><p className="mt-1 text-sm text-muted-foreground">選取同一主題的已索引文件，最多 {MAX_DOCUMENTS} 份。</p></div><span className="rounded-full bg-secondary px-2.5 py-1 text-xs text-muted-foreground">{selected.length} 份已選</span></div>
                            {available.length ? <div className="mt-4 max-h-64 space-y-1 overflow-y-auto rounded-lg border border-border p-2">{available.map((doc) => <label key={doc.id} className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-sm hover:bg-accent"><input type="checkbox" checked={selected.includes(doc.id)} disabled={selected.length >= MAX_DOCUMENTS && !selected.includes(doc.id)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, doc.id].slice(0, MAX_DOCUMENTS) : current.filter((id) => id !== doc.id))} /><FileText size={16} className="shrink-0 text-primary" /><span className="truncate">{documentDisplayName(doc)}</span></label>)}</div> : <p className="mt-4 rounded-lg border border-dashed border-border p-5 text-sm text-muted-foreground">目前沒有可用來源。請先至知識庫上傳 PDF、DOCX、Markdown 或 TXT 文件並等待索引完成。</p>}
                            <Button variant="outline" className="mt-4" onClick={onBrowseSources}>管理知識庫</Button>
                        </div>
                        <div className="pt-7"><h2 className="font-semibold">撰寫指引</h2><p className="mt-1 text-sm text-muted-foreground">選填：指定新聞角度或需強調的來源內容。稿件仍以文件事實為準。</p><Textarea className="mt-4 min-h-28" maxLength={4000} value={guidance} onChange={(event) => setGuidance(event.target.value)} placeholder="例如：聚焦新制對符合資格民眾的影響" /></div>
                        {(busy || job.job) && <div className="mt-6 rounded-lg border border-border bg-secondary/50 p-4 text-sm" role="status">{phaseLabels[job.job?.phase || "queued"] || "處理中"}{busy ? "，請稍候…" : ""}</div>}
                        {(job.error || job.job?.error) && <p className="mt-4 text-sm text-destructive" role="alert">{job.error?.message || job.job?.error}</p>}
                        {job.warning && <p className="mt-4 text-sm text-amber-700">{job.warning}</p>}
                        <Button className="mt-6" disabled={!selected.length || busy} onClick={() => { void job.start({ document_ids: selected, guidance }).catch(() => undefined); }}>{busy ? "生成中…" : "生成新聞稿"}</Button>
                    </>
                )}
            </div>
        </section>
    );
}
