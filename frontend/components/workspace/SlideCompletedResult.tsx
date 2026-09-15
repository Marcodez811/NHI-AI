"use client";

import { CheckCircle2, Download } from "lucide-react";

import type { DocumentRead, SlideJob } from "../../lib/api/slides";
import { Button } from "../ui/button";
import { documentDisplayName, formatDateTime } from "./WorkspaceViewUtils";

export function SlideCompletedResult({
    job,
    docs,
    title,
    downloadUrl,
    onNewPresentation,
}: {
    job: SlideJob;
    docs: DocumentRead[];
    title: string;
    downloadUrl: string;
    onNewPresentation: () => void;
}) {
    const sourceNames = job.brief?.document_ids.map((id) => {
        const source = docs.find((document) => document.id === id);
        return source ? documentDisplayName(source) : "來源已不存在";
    }) ?? [];
    const sourceSummary = sourceNames.length === 0
        ? "—"
        : sourceNames.length === 1
          ? sourceNames[0]
          : `${sourceNames[0]} 等 ${sourceNames.length} 份`;

    return (
        <section className="px-5 py-8 lg:px-10 lg:py-10">
            <div className="mx-auto max-w-[560px]">
                <div className="mb-7">
                    <h1 className="mt-1 text-2xl font-semibold tracking-tight">簡報已準備好</h1>
                    <p className="mt-1 text-sm text-muted-foreground">
                        這次工作已完成。你可以下載檔案，或用相同設定建立下一份簡報。
                    </p>
                </div>
                <div className="max-w-3xl rounded-2xl border border-border bg-card p-6 shadow-sm">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                        <div>
                            <p className="text-xs text-muted-foreground">簡報標題</p>
                            <h2 className="mt-1 text-xl font-semibold">{job.brief?.title ?? title}</h2>
                        </div>
                        <a
                            href={downloadUrl}
                            download
                            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90"
                        >
                            <Download size={15} />
                            下載 PPTX
                        </a>
                    </div>
                    <div className="mt-6 flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                        <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
                        <span>簡報已完成驗證，可以下載檔案。</span>
                    </div>
                    <dl className="mt-6 grid gap-4 border-t border-border pt-5 text-sm sm:grid-cols-3">
                        <div className="min-w-0">
                            <dt className="text-xs text-muted-foreground">來源</dt>
                            <dd className="mt-1 truncate font-medium" title={sourceNames.join("、")}>
                                {sourceSummary}
                            </dd>
                        </div>
                        <div>
                            <dt className="text-xs text-muted-foreground">開始時間</dt>
                            <dd className="mt-1 font-medium">{formatDateTime(job.started_at)}</dd>
                        </div>
                        <div>
                            <dt className="text-xs text-muted-foreground">完成時間</dt>
                            <dd className="mt-1 font-medium">{formatDateTime(job.finished_at)}</dd>
                        </div>
                    </dl>
                    <Button type="button" onClick={onNewPresentation} variant="outline" className="mt-5">
                        建立新簡報
                    </Button>
                </div>
            </div>
        </section>
    );
}
