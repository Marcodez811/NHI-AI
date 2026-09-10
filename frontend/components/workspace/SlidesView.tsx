"use client";

import { useState } from "react";
import {
    CheckCircle2,
    CircleAlert,
    Download,
    FilePlus2,
    FileText,
    Library,
    Search,
    Sparkles,
    Upload,
    X,
} from "lucide-react";
import { AgentJobActivity } from "./AgentJobActivity";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Textarea } from "../ui/textarea";
import type { AgentJobClientPhase } from "../../lib/hooks/useAgentJob";
import type {
    AgentJobPhase,
    DocumentRead,
    SlideJob,
} from "../../lib/api/slides";
import {
    MAX_DOCUMENTS,
    SUPPORTED_SLIDE_EXTENSIONS,
    slideDownloadUrl,
} from "../../lib/api/slides";
import type { Tone } from "../../lib/workspace/types";
import {
    documentDisplayName,
    extensionOf,
    formatDateTime,
    statusLabel,
} from "./WorkspaceViewUtils";

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
    start: () => Promise<void>;
    retry: () => Promise<void>;
    onNewPresentation?: () => void;
}) {
    const selectedDocs = docs.filter((doc) => selected.includes(doc.id));
    const eligible = selectedDocs.filter(
        (doc) =>
            doc.status === "ready" &&
            (SUPPORTED_SLIDE_EXTENSIONS as readonly string[]).includes(
                extensionOf(doc),
            ),
    );
    const unsupported = selectedDocs.filter(
        (doc) =>
            doc.status === "ready" &&
            !(SUPPORTED_SLIDE_EXTENSIONS as readonly string[]).includes(
                extensionOf(doc),
            ),
    );
    const notReady = selectedDocs.filter((doc) => doc.status !== "ready");
    const failedSources = notReady.filter((doc) => doc.status === "failed");
    const deletingSources = notReady.filter((doc) =>
        ["deleting", "delete_failed"].includes(doc.status),
    );
    const availableSources = docs.filter(
        (doc) =>
            doc.status === "ready" &&
            (SUPPORTED_SLIDE_EXTENSIONS as readonly string[]).includes(
                extensionOf(doc),
            ),
    );
    const [sourcePickerOpen, setSourcePickerOpen] = useState(false);
    const [sourceQuery, setSourceQuery] = useState("");
    const sourceOptions = availableSources.filter((doc) => {
        const query = sourceQuery.trim().toLowerCase();
        return (
            !query ||
            documentDisplayName(doc).toLowerCase().includes(query) ||
            doc.original_filename.toLowerCase().includes(query)
        );
    });
    const completed = job?.status === "completed";
    const failed = job?.status === "failed";
    const busy = phase === "submitting" || phase === "polling";
    const canGenerate =
        !busy &&
        Boolean(title.trim()) &&
        selectedDocs.length > 0 &&
        notReady.length === 0 &&
        unsupported.length === 0;
    const blockedCount = selectedDocs.length - eligible.length;
    const sourceFormats = "PDF、DOCX、Markdown、TXT";
    const readinessMessage = busy
        ? "正在建立簡報，請稍候。"
        : canGenerate
          ? "來源已就緒，可以生成。"
          : phase === "expired"
            ? "這次簡報工作已過期，請重新生成。"
            : !title.trim()
              ? "請先輸入簡報標題。"
              : !selectedDocs.length
                ? "選取至少一份可用來源後即可生成。"
                : notReady.length > 0
                  ? failedSources.length
                    ? failedSources.length + " 份選取文件索引失敗，請在知識庫重新上傳或移除。"
                    : deletingSources.length
                      ? deletingSources.length + " 份選取文件正在刪除，請移除後再生成。"
                      : notReady.length + " 份選取文件正在索引，完成後才能生成。"
                  : unsupported.length > 0
                    ? "請移除不支援的來源格式後再生成。"
                    : blockedCount > 0
                      ? "請確認所有選取來源都已就緒。"
                      : failed
                        ? "這次簡報生成失敗，請選擇再次生成。"
                        : "來源已就緒，可以生成。";
    const availabilityMessage = docs.length
        ? availableSources.length
            ? "知識庫目前有 " + availableSources.length + " 份可用來源。"
            : docs.some((doc) => ["queued", "indexing"].includes(doc.status))
              ? "來源正在索引，完成後會出現在可選清單。"
              : "知識庫中的文件尚未完成索引，或格式尚不支援。"
        : "知識庫目前沒有文件。";
    const downloadUrl = job ? slideDownloadUrl(job) : null;
    const jobBrief = job?.brief ?? null;
    const jobSourceNames = jobBrief?.document_ids.map((id) => {
        const source = docs.find((doc) => doc.id === id);
        return source ? documentDisplayName(source) : "來源已不存在";
    }) ?? [];
    const beginNewPresentation = () => {
        if (onNewPresentation) onNewPresentation();
        else void retry();
    };

    if (completed && downloadUrl) {
        return (
            <section className="p-5 lg:p-8">
                <div className="mx-auto max-w-5xl">
                    <div className="mb-7">
                        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
                            簡報已準備好
                        </h1>
                        <p className="mt-1 text-sm text-muted-foreground">
                            這次工作已完成。你可以下載檔案，或用相同設定建立下一份簡報。
                        </p>
                    </div>
                    <div className="max-w-3xl rounded-2xl border border-border bg-card p-6 shadow-sm">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                            <div>
                                <p className="text-xs text-muted-foreground">
                                    簡報標題
                                </p>
                                <h2 className="mt-1 text-xl font-semibold">
                                    {jobBrief?.title ?? title}
                                </h2>
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
                            <CheckCircle2
                                size={16}
                                className="mt-0.5 shrink-0"
                            />
                            <span>簡報已完成驗證，可以下載檔案。</span>
                        </div>
                        <dl className="mt-6 grid gap-4 border-t border-border pt-5 text-sm sm:grid-cols-3">
                            <div className="min-w-0">
                                <dt className="text-xs text-muted-foreground">
                                    來源
                                </dt>
                                <dd
                                    className="mt-1 truncate font-medium"
                                    title={jobSourceNames.join("、")}
                                >
                                    {jobSourceNames.length
                                        ? jobSourceNames.length === 1
                                            ? jobSourceNames[0]
                                            : `${jobSourceNames[0]} 等 ${jobSourceNames.length} 份`
                                        : "—"}
                                </dd>
                            </div>
                            <div>
                                <dt className="text-xs text-muted-foreground">
                                    開始時間
                                </dt>
                                <dd className="mt-1 font-medium">
                                    {formatDateTime(job.started_at)}
                                </dd>
                            </div>
                            <div>
                                <dt className="text-xs text-muted-foreground">
                                    完成時間
                                </dt>
                                <dd className="mt-1 font-medium">
                                    {formatDateTime(job.finished_at)}
                                </dd>
                            </div>
                        </dl>
                        <Button
                            type="button"
                            onClick={beginNewPresentation}
                            variant="outline"
                            className="mt-5"
                        >
                            建立新簡報
                        </Button>
                    </div>
                </div>
            </section>
        );
    }

    return (
        <section className="p-5 lg:p-8">
            <div className="mx-auto max-w-5xl">
                <div className="mb-7">
                    <h1 className="text-2xl font-semibold tracking-tight">
                        生成簡報
                    </h1>
                    <p className="mt-1 text-sm text-muted-foreground">
                        先選取來源，再調整設定，建立結構清晰的政策簡報。
                    </p>
                </div>

                <div className="grid items-start gap-5 xl:grid-cols-[1.1fr_.9fr]">
                    <div className="h-fit rounded-2xl border border-border bg-card p-6 shadow-sm">
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
                                        const supported =
                                            doc.status === "ready" &&
                                            (
                                                SUPPORTED_SLIDE_EXTENSIONS as readonly string[]
                                            ).includes(extensionOf(doc));
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

                        {sourcePickerOpen && (
                            <div className="mt-5 rounded-xl border border-border bg-background p-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <h3 className="text-sm font-semibold">
                                            選擇來源文件
                                        </h3>
                                        <p className="mt-1 text-xs text-muted-foreground">
                                            只顯示已完成索引且格式支援的文件。
                                        </p>
                                    </div>
                                    <Button
                                        type="button"
                                        onClick={() =>
                                            setSourcePickerOpen(false)
                                        }
                                        aria-label="關閉來源選擇"
                                        variant="ghost"
                                        size="icon-sm"
                                    >
                                        <X size={15} />
                                    </Button>
                                </div>
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
                                <div className="mt-3 flex items-center justify-between gap-3 border-t border-border pt-3 text-xs text-muted-foreground">
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
                                </div>
                            </div>
                        )}
                    </div>

                    <div className="flex h-fit flex-col gap-5 rounded-2xl border border-border bg-card p-6 shadow-sm">
                        <div>
                            <h2 className="font-semibold">生成設定</h2>
                            <p className="mt-1 text-sm text-muted-foreground">
                                調整簡報標題、頁數與語氣。
                            </p>
                        </div>

                        <div className="space-y-5">
                            <label
                                htmlFor="slide-title"
                                className="flex flex-col gap-2 text-sm font-medium"
                            >
                                簡報標題
                                <Input
                                    id="slide-title"
                                    value={title}
                                    onChange={(event) =>
                                        setTitle(event.target.value)
                                    }
                                    className="font-normal"
                                />
                            </label>
                            <label
                                htmlFor="slide-count"
                                className="flex flex-col gap-2 text-sm font-medium"
                            >
                                投影片張數
                                <Input
                                    id="slide-count"
                                    type="number"
                                    min={5}
                                    max={25}
                                    value={count}
                                    onChange={(event) =>
                                        setCount(
                                            Math.min(
                                                25,
                                                Math.max(
                                                    5,
                                                    Number(
                                                        event.target.value,
                                                    ) || 5,
                                                ),
                                            ),
                                        )
                                    }
                                    className="font-normal"
                                />
                            </label>
                            <div className="flex flex-col gap-2 text-sm font-medium">
                                語氣
                                <div
                                    role="group"
                                    aria-label="語氣"
                                    className="grid grid-cols-2 gap-2"
                                >
                                    {(
                                        [
                                            ["formal", "正式"],
                                            ["casual", "輕鬆"],
                                        ] as const
                                    ).map(([value, label]) => (
                                        <Button
                                            type="button"
                                            aria-pressed={tone === value}
                                            onClick={() => setTone(value)}
                                            variant={
                                                tone === value
                                                    ? "secondary"
                                                    : "outline"
                                            }
                                            className={
                                                tone === value
                                                    ? "h-auto justify-start border-primary bg-primary/10 p-3 text-left text-primary"
                                                    : "h-auto justify-start p-3 text-left"
                                            }
                                            key={value}
                                        >
                                            {label}
                                        </Button>
                                    ))}
                                </div>
                            </div>
                            <label
                                htmlFor="slide-guidance"
                                className="flex flex-col gap-2 text-sm font-medium"
                            >
                                補充指引{" "}
                                <span className="font-normal text-muted-foreground">
                                    選填
                                </span>
                                <Textarea
                                    id="slide-guidance"
                                    value={guidance}
                                    onChange={(event) =>
                                        setGuidance(event.target.value)
                                    }
                                    rows={4}
                                    placeholder="例如：聚焦政策影響，以主管簡報語氣撰寫。"
                                    className="resize-none font-normal"
                                />
                            </label>
                        </div>

                        {error && !job && (
                            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-xs text-red-700">
                                <CircleAlert size={14} />
                                {error}
                            </div>
                        )}
                        {warning && !job && (
                            <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-xs text-amber-800">
                                <CircleAlert
                                    size={14}
                                    className="mt-0.5 shrink-0"
                                />
                                <div className="min-w-0 flex-1">
                                    <p>{warning}</p>
                                    {pollNow && (
                                        <Button
                                            type="button"
                                            onClick={() => void pollNow()}
                                            variant="link"
                                            size="sm"
                                            className="mt-1.5 h-auto p-0 font-medium"
                                        >
                                            立即重試
                                        </Button>
                                    )}
                                </div>
                            </div>
                        )}
                        {job && (busy || failed || completed) && (
                            <AgentJobActivity
                                job={job}
                                clientPhase={phase}
                                phaseHistory={phaseHistory}
                                warning={warning}
                                error={error}
                                workflowLabel="簡報"
                                onRefresh={
                                    pollNow ? () => void pollNow() : undefined
                                }
                                onRetry={beginNewPresentation}
                            />
                        )}
                        {phase === "expired" && (
                            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
                                簡報工作已過期或不存在，請重新生成。
                            </div>
                        )}

                        <div
                            aria-live="polite"
                            className={
                                canGenerate
                                    ? "flex items-start gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-3 text-sm text-emerald-800"
                                    : "flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-sm text-amber-800"
                            }
                        >
                            {canGenerate ? (
                                <CheckCircle2
                                    size={16}
                                    className="mt-0.5 shrink-0"
                                />
                            ) : (
                                <CircleAlert
                                    size={16}
                                    className="mt-0.5 shrink-0"
                                />
                            )}
                            <span>{readinessMessage}</span>
                        </div>
                        <Button
                            type="button"
                            onClick={() => void start()}
                            disabled={!canGenerate}
                            variant={canGenerate ? "default" : "secondary"}
                            className={
                                canGenerate
                                    ? "w-full gap-2 py-2.5"
                                    : "w-full cursor-not-allowed gap-2 py-2.5"
                            }
                        >
                            <Sparkles size={16} />
                            {phase === "submitting" ? "送出中…" : "生成簡報"}
                        </Button>
                        {selected.length > 0 &&
                            (notReady.length > 0 || unsupported.length > 0) && (
                                <p className="text-xs text-muted-foreground">
                                    {notReady.length
                                        ? notReady.length +
                                          " 份文件尚未就緒，完成索引後才能生成"
                                        : ""}
                                    {unsupported.length
                                        ? (notReady.length ? "；" : "") +
                                          unsupported.length +
                                          " 份文件格式不支援，請先移除"
                                        : ""}
                                    。
                                </p>
                            )}
                    </div>
                </div>
            </div>
        </section>
    );
}
