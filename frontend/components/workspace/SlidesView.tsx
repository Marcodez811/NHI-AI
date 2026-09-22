"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
    FilePlus2,
    FileText,
    Library,
    Search,
    Upload,
    X,
} from "lucide-react";
import { Button } from "../ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../ui/dialog";
import { Input } from "../ui/input";
import type { AgentJobClientPhase } from "../../lib/hooks/useAgentJob";
import type {
    AgentJobPhase,
    DocumentRead,
    SlideJob,
    SlideJobSummary,
} from "../../lib/api/slides";
import {
    MAX_DOCUMENTS,
    slideDownloadUrl,
} from "../../lib/api/slides";
import type { Tone } from "../../lib/workspace/types";
import {
    documentDisplayName,
    extensionOf,
    statusLabel,
} from "./WorkspaceViewUtils";
import { analyzeSlideReadiness } from "./slide-readiness";
import { SlideSettings } from "./SlideSettings";
import { SlideGenerationStatus } from "./SlideGenerationStatus";
import { SlideCompletedResult } from "./SlideCompletedResult";
import { OutlineReview } from "./OutlineReview";
import { RecentSlideJobs } from "./RecentSlideJobs";

export function SlidesView({
    docs,
    selected,
    setSelected,
    onUpload,
    onBrowseSources,
    title,
    setTitle,
    count,
    setCount,
    guidance,
    setGuidance,
    tone,
    setTone,
    job,
    phase,
    phaseHistory = [],
    error,
    warning,
    pollNow,
    resumePolling,
    start,
    retry,
    onNewPresentation,
}: {
    docs: DocumentRead[];
    selected: string[];
    setSelected: React.Dispatch<React.SetStateAction<string[]>>;
    onUpload: () => void;
    onBrowseSources: () => void;
    title: string;
    setTitle: (value: string) => void;
    count: number;
    setCount: (value: number) => void;
    guidance: string;
    setGuidance: (value: string) => void;
    tone: Tone;
    setTone: (value: Tone) => void;
    job: SlideJob | null;
    phase: AgentJobClientPhase;
    phaseHistory?: AgentJobPhase[];
    error: string | null;
    warning: string | null;
    pollNow?: () => Promise<SlideJob | undefined>;
    resumePolling?: () => void;
    start: () => Promise<void>;
    retry: () => Promise<void>;
    onNewPresentation?: () => void;
}) {
    const {
        selectedDocs,
        eligible,
        unsupported,
        notReady,
        availableSources,
        canGenerate,
        readinessMessage,
        availabilityMessage,
    } = analyzeSlideReadiness({ docs, selected, title, phase, jobStatus: job?.status });
    const [sourcePickerOpen, setSourcePickerOpen] = useState(false);
    const [sourceQuery, setSourceQuery] = useState("");
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    // Unknown or missing ?tab means the new-presentation form, per the "no state-dependent default" rule.
    const activeTab: "new" | "recent" = searchParams.get("tab") === "recent" ? "recent" : "new";
    const pageHeader =
        activeTab === "recent"
            ? {
                  title: "最近的簡報工作",
                  subtitle: "從這裡返回待審核的大綱，或開啟已完成的簡報。",
              }
            : {
                  title: "生成簡報",
                  subtitle: "先選取來源，再調整設定，建立結構清晰的政策簡報。",
              };
    const [recentJobs, setRecentJobs] = useState<SlideJobSummary[] | null>(null);
    const awaitingReviewCount = useMemo(
        () =>
            (recentJobs ?? []).filter(
                (recentJob) => recentJob.status === "awaiting_input" && recentJob.phase === "awaiting_outline",
            ).length,
        [recentJobs],
    );
    const selectTab = useCallback(
        (next: "new" | "recent") => {
            const params = new URLSearchParams(searchParams.toString());
            if (next === "recent") params.set("tab", "recent");
            else params.delete("tab");
            const query = params.toString();
            // A pushed entry (not replace) is what lets the back button step between tabs.
            router.push(query ? `${pathname}?${query}` : pathname);
        },
        [pathname, router, searchParams],
    );
    const sourceOptions = availableSources.filter((doc) => {
        const query = sourceQuery.trim().toLowerCase();
        return (
            !query ||
            documentDisplayName(doc).toLowerCase().includes(query) ||
            doc.original_filename.toLowerCase().includes(query)
        );
    });
    const completed = job?.status === "completed";
    const awaitingOutline = job?.status === "awaiting_input" && job.phase === "awaiting_outline";
    const sourceFormats = "PDF、DOCX、Markdown、TXT";
    const downloadUrl = job ? slideDownloadUrl(job) : null;
    const beginNewPresentation = () => {
        if (onNewPresentation) onNewPresentation();
        else void retry();
    };

    if (completed && downloadUrl) {
        return (
            <SlideCompletedResult
                job={job}
                docs={docs}
                title={title}
                downloadUrl={downloadUrl}
                onNewPresentation={beginNewPresentation}
            />
        );
    }

    return (
        <section className="px-5 py-8 lg:px-10 lg:py-10">
            <div className="mx-auto max-w-[720px]">
                <div className="mb-7">
                    <Link href="/workflows" className="mb-5 inline-flex items-center text-xs text-info hover:text-info/80">← 返回 AI 工作流</Link>
                    <h1 className="text-[24px] font-semibold tracking-tight">
                        {pageHeader.title}
                    </h1>
                    <p className="mt-1 text-sm text-muted-foreground">
                        {pageHeader.subtitle}
                    </p>
                </div>

                <div role="tablist" aria-label="簡報作業" className="mb-7 flex gap-6 border-b border-border">
                    <button
                        type="button"
                        role="tab"
                        id="slides-tab-new"
                        aria-selected={activeTab === "new"}
                        aria-controls="slides-panel-new"
                        tabIndex={activeTab === "new" ? 0 : -1}
                        onClick={() => selectTab("new")}
                        className={`-mb-px border-b-2 px-1 pb-3 text-sm font-medium transition-colors ${
                            activeTab === "new"
                                ? "border-primary text-foreground"
                                : "border-transparent text-muted-foreground hover:text-foreground"
                        }`}
                    >
                        新簡報
                    </button>
                    <button
                        type="button"
                        role="tab"
                        id="slides-tab-recent"
                        aria-selected={activeTab === "recent"}
                        aria-controls="slides-panel-recent"
                        tabIndex={activeTab === "recent" ? 0 : -1}
                        onClick={() => selectTab("recent")}
                        className={`-mb-px border-b-2 px-1 pb-3 text-sm font-medium transition-colors ${
                            activeTab === "recent"
                                ? "border-primary text-foreground"
                                : "border-transparent text-muted-foreground hover:text-foreground"
                        }`}
                    >
                        最近工作
                        {awaitingReviewCount > 0 && (
                            <span className="text-amber-700 dark:text-amber-300"> · {awaitingReviewCount} 待審核</span>
                        )}
                    </button>
                </div>

                <div
                    id="slides-panel-new"
                    role="tabpanel"
                    aria-labelledby="slides-tab-new"
                    hidden={activeTab !== "new"}
                    className="space-y-0"
                >
                    <div className="h-fit border-b border-border pb-7">
                        <div className="flex items-start justify-between gap-4">
                            <div>
                                <h2 className="font-semibold">來源文件</h2>
                                <p className="mt-1 text-sm text-muted-foreground">
                                    建立簡報前，先加入要引用的文件。
                                </p>
                            </div>
                            <span className="shrink-0 rounded-full bg-secondary px-2.5 py-1 text-xs text-muted-foreground">
                                {selectedDocs.length
                                    ? eligible.length + " 份可用"
                                    : availableSources.length + " 份可選"}
                            </span>
                        </div>

                        {selectedDocs.length > 0 && (
                            <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-border bg-secondary/60 px-3 py-2.5 text-xs">
                                <span className="text-muted-foreground">
                                    來源索引狀態
                                </span>
                                <span
                                    className={
                                        notReady.length
                                            ? "font-medium text-amber-700"
                                            : "font-medium text-emerald-700"
                                    }
                                >
                                    {selectedDocs.length - notReady.length} /{" "}
                                    {selectedDocs.length} 份已就緒
                                </span>
                            </div>
                        )}

                        {!selectedDocs.length ? (
                            <div className="mt-5 rounded-xl border border-dashed border-primary/30 bg-primary/[.03] p-6">
                                <div className="flex size-11 items-center justify-center rounded-xl bg-primary/10 text-primary">
                                    <FilePlus2 size={22} />
                                </div>
                                <h3 className="mt-4 font-semibold">
                                    先加入來源文件
                                </h3>
                                <p className="mt-1 max-w-md text-sm leading-6 text-muted-foreground">
                                    簡報內容會根據來源文件整理。支援{" "}
                                    {sourceFormats} 格式。
                                </p>
                                <div className="mt-5 flex flex-wrap gap-2">
                                    {availableSources.length > 0 ? (
                                        <>
                                            <Button
                                                type="button"
                                                onClick={() =>
                                                    setSourcePickerOpen(true)
                                                }
                                                className="gap-2"
                                            >
                                                <Library size={15} />
                                                選取來源文件
                                            </Button>
                                            <Button
                                                type="button"
                                                onClick={onUpload}
                                                variant="outline"
                                                className="gap-2"
                                            >
                                                <Upload size={15} />
                                                上傳來源文件
                                            </Button>
                                        </>
                                    ) : (
                                        <>
                                            <Button
                                                type="button"
                                                onClick={onUpload}
                                                className="gap-2"
                                            >
                                                <Upload size={15} />
                                                上傳來源文件
                                            </Button>
                                            <Button
                                                type="button"
                                                onClick={onBrowseSources}
                                                variant="outline"
                                                className="gap-2"
                                            >
                                                <Library size={15} />
                                                前往知識庫
                                            </Button>
                                        </>
                                    )}
                                </div>
                                <p className="mt-4 text-xs text-muted-foreground">
                                    {availabilityMessage}
                                </p>
                            </div>
                        ) : (
                            <>
                                <div className="mt-5 space-y-2">
                                    {selectedDocs.map((doc) => {
                                        const supported = !unsupported.some(
                                            (source) => source.id === doc.id,
                                        );
                                        return (
                                            <div
                                                className="rounded-lg border border-border bg-background px-3 py-3"
                                                key={doc.id}
                                            >
                                                <div className="flex items-start justify-between gap-3">
                                                    <span className="flex min-w-0 items-center gap-2 text-sm">
                                                        <FileText
                                                            size={15}
                                                            className="shrink-0 text-primary"
                                                        />
                                                        <span className="truncate">
                                                            {documentDisplayName(
                                                                doc,
                                                            )}
                                                        </span>
                                                    </span>
                                                    <Button
                                                        type="button"
                                                        onClick={() =>
                                                            setSelected(
                                                                (items) =>
                                                                    items.filter(
                                                                        (id) =>
                                                                            id !==
                                                                            doc.id,
                                                                    ),
                                                            )
                                                        }
                                                        aria-label={
                                                            "移除 " +
                                                            documentDisplayName(
                                                                doc,
                                                            )
                                                        }
                                                        variant="ghost"
                                                        size="icon-sm"
                                                        className="text-muted-foreground"
                                                    >
                                                        <X size={15} />
                                                    </Button>
                                                </div>
                                                {doc.status !== "ready" && (
                                                    <p className="mt-1 pl-6 text-xs text-amber-600">
                                                        {statusLabel(
                                                            doc.status,
                                                        )}
                                                        ，完成索引後才可使用
                                                    </p>
                                                )}
                                                {doc.status === "ready" &&
                                                    !supported && (
                                                        <p className="mt-1 pl-6 text-xs text-red-600">
                                                            不支援的來源格式（
                                                            {extensionOf(doc) ||
                                                                "未知"}
                                                            ）
                                                        </p>
                                                    )}
                                            </div>
                                        );
                                    })}
                                </div>
                                <div className="mt-4 flex flex-wrap gap-2">
                                    <Button
                                        type="button"
                                        onClick={() =>
                                            setSourcePickerOpen(true)
                                        }
                                        variant="outline"
                                    >
                                        新增來源
                                    </Button>
                                    <Button
                                        type="button"
                                        onClick={onBrowseSources}
                                        variant="ghost"
                                    >
                                        管理知識庫
                                    </Button>
                                    <Button
                                        type="button"
                                        onClick={() => setSelected([])}
                                        variant="ghost"
                                    >
                                        清除選取
                                    </Button>
                                </div>
                            </>
                        )}

                        <Dialog open={sourcePickerOpen} onOpenChange={setSourcePickerOpen}>
                            <DialogContent className="sm:max-w-lg">
                                <DialogHeader>
                                    <DialogTitle>選擇來源文件</DialogTitle>
                                    <DialogDescription>只顯示已完成索引且格式支援的文件。</DialogDescription>
                                </DialogHeader>
                                <div className="mt-3 flex items-center gap-2 rounded-md border border-border bg-card px-3">
                                    <Search
                                        size={15}
                                        className="text-muted-foreground"
                                    />
                                    <Input
                                        value={sourceQuery}
                                        onChange={(event) =>
                                            setSourceQuery(event.target.value)
                                        }
                                        placeholder="搜尋文件名稱…"
                                        aria-label="搜尋來源文件"
                                        className="h-8 border-0 bg-transparent py-2 text-sm shadow-none focus-visible:ring-0"
                                    />
                                </div>
                                <div className="mt-3 max-h-56 space-y-1 overflow-y-auto">
                                    {sourceOptions.map((doc) => (
                                        <label
                                            key={doc.id}
                                            className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-sm hover:bg-accent"
                                        >
                                            <input
                                                type="checkbox"
                                                checked={selected.includes(
                                                    doc.id,
                                                )}
                                                onChange={(event) =>
                                                    setSelected((items) =>
                                                        event.target.checked
                                                            ? Array.from(
                                                                  new Set([
                                                                      ...items,
                                                                      doc.id,
                                                                  ]),
                                                              ).slice(
                                                                  0,
                                                                  MAX_DOCUMENTS,
                                                              )
                                                            : items.filter(
                                                                  (id) =>
                                                                      id !==
                                                                      doc.id,
                                                              ),
                                                    )
                                                }
                                            />
                                            <FileText
                                                size={15}
                                                className="shrink-0 text-primary"
                                            />
                                            <span className="min-w-0 truncate">
                                                {documentDisplayName(doc)}
                                            </span>
                                        </label>
                                    ))}
                                    {!sourceOptions.length && (
                                        <p className="px-2 py-4 text-center text-xs text-muted-foreground">
                                            找不到符合條件的可用文件。
                                        </p>
                                    )}
                                </div>
                                <DialogFooter>
                                    <span>
                                        已選取 {selected.length} /{" "}
                                        {MAX_DOCUMENTS}
                                    </span>
                                    <Button
                                        type="button"
                                        onClick={() =>
                                            setSourcePickerOpen(false)
                                        }
                                        size="sm"
                                    >
                                        完成
                                    </Button>
                                </DialogFooter>
                            </DialogContent>
                        </Dialog>
                    </div>

                    <div className="flex h-fit flex-col gap-5 pt-7">
                        <div>
                            <h2 className="font-semibold">生成設定</h2>
                            <p className="mt-1 text-sm text-muted-foreground">
                                調整簡報標題、頁數與語氣。
                            </p>
                        </div>

                        <SlideSettings
                            title={title}
                            setTitle={setTitle}
                            count={count}
                            setCount={setCount}
                            guidance={guidance}
                            setGuidance={setGuidance}
                            tone={tone}
                            setTone={setTone}
                        />

                        {awaitingOutline && job && <OutlineReview jobId={job.job_id} onApproved={resumePolling} />}

                        <SlideGenerationStatus
                            job={job}
                            phase={phase}
                            phaseHistory={phaseHistory}
                            error={error}
                            warning={warning}
                            pollNow={pollNow}
                            retry={beginNewPresentation}
                            canGenerate={canGenerate}
                            readinessMessage={readinessMessage}
                            selectedCount={selected.length}
                            notReadyCount={notReady.length}
                            unsupportedCount={unsupported.length}
                            start={start}
                        />
                    </div>
                </div>

                <div
                    id="slides-panel-recent"
                    role="tabpanel"
                    aria-labelledby="slides-tab-recent"
                    hidden={activeTab !== "recent"}
                >
                    <RecentSlideJobs onJobsChange={setRecentJobs} />
                </div>
            </div>
        </section>
    );
}
