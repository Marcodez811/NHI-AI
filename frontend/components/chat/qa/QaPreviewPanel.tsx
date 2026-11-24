"use client";

import { useState } from "react";
import { CircleAlert, Download, Loader2, Pencil, X } from "lucide-react";
import { artifactDownloadUrl } from "../../../lib/api/artifacts";
import type { QaDocQuestion, QaDocSection, QaDocument, QaIssue, QaRevisionInput, QaVersion } from "../../../lib/api/qa";
import { useQaVersion } from "../../../lib/hooks/useQaVersion";
import {
    changedKey,
    changedParagraphs,
    issuesOf,
    paragraphKey,
    paragraphLabel,
    partSections,
    placeIssues,
    sectionTitle,
} from "../../../lib/qa/preview";
import type { PartIssues, PreviewPart } from "../../../lib/qa/preview";
import { cn } from "../../../lib/utils";
import { Badge } from "../../ui/badge";
import { Button, buttonVariants } from "../../ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../ui/select";
import { Textarea } from "../../ui/textarea";
import { ErrorLine, GenerationStatus, ISSUE_KIND_LABELS, useRun, versionTime } from "./QaCards";

const PART_LABELS: Record<PreviewPart, string> = { short: "簡答", detail: "詳答" };

const versionLabel = (version: QaVersion): string =>
    `${version.kind === "ai" ? "AI 撰寫" : "大綱版"}${version.revised_from ? "（修訂）" : ""}　${versionTime(version.created_at)}`;

/** ⚠ beside a paragraph, section heading or part: the message on hover (title) and on click. */
function IssueMarks({ issues }: { issues: QaIssue[] }) {
    const [open, setOpen] = useState(false);
    if (issues.length === 0) return null;
    const summary = issues.map((issue) => issue.message).join("\n");
    return (
        <>
            <button type="button" title={summary} aria-expanded={open} aria-label={`待確認 ${issues.length} 項`}
                onClick={() => setOpen((value) => !value)} data-qa-issue-marker
                className="ml-1 inline-flex shrink-0 items-center rounded-md px-1 text-xs font-medium text-destructive hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                ⚠{issues.length > 1 ? issues.length : ""}
            </button>
            {open && (
                <ul className="mt-1 flex w-full flex-col gap-1 rounded-lg bg-destructive/5 p-2 text-xs">
                    {issues.map((issue, index) => (
                        <li key={index} className="flex flex-wrap items-baseline gap-x-1.5 break-words">
                            <Badge variant="outline">{ISSUE_KIND_LABELS[issue.kind] ?? issue.kind}</Badge>
                            <span className="text-muted-foreground">{issue.message}</span>
                        </li>
                    ))}
                </ul>
            )}
        </>
    );
}

function SectionView({ section, index, placed, changed, keyOf }: {
    section: QaDocSection; index: number; placed: PartIssues; changed: Set<string>;
    keyOf: (paragraph: number) => string;
}) {
    const title = sectionTitle(section, index);
    return (
        <div className="flex flex-col gap-1.5">
            {title && (
                <h4 className="flex flex-wrap items-baseline font-semibold">
                    {title}
                    <IssueMarks issues={placed.bySection.get(index) ?? []} />
                </h4>
            )}
            {section.paragraphs.map((paragraph, number) => {
                const marks = placed.byParagraph.get(paragraphKey(index, number)) ?? [];
                const isChanged = changed.has(keyOf(number));
                return (
                    <div key={number} data-qa-paragraph data-changed={isChanged || undefined}
                        className={cn("flex items-baseline gap-1.5 rounded-md px-1.5 py-0.5", title && "ml-4",
                            isChanged && "bg-primary/10", marks.length > 0 && "ring-1 ring-destructive/30")}>
                        <span className="shrink-0 text-muted-foreground">{paragraphLabel(section, number)}</span>
                        <span className="min-w-0 flex-1 whitespace-pre-line break-words">
                            {paragraph.text}
                            {paragraph.cites.map((cite) => (
                                <sup key={cite} className="ml-0.5 text-[0.7em] text-muted-foreground">[{cite}]</sup>
                            ))}
                        </span>
                        <IssueMarks issues={marks} />
                    </div>
                );
            })}
        </div>
    );
}

function PartView({ document, question, part, base }: {
    document: QaDocument; question: QaDocQuestion; part: PreviewPart; base: QaDocument | null;
}) {
    const sections = partSections(question, part);
    const placed = placeIssues(issuesOf(document, question.no, part), sections);
    const changed = base ? changedParagraphs(base, document) : new Set<string>();
    return (
        <div className="flex flex-col gap-3">
            {placed.top.length > 0 && (
                <div className="flex flex-wrap items-baseline rounded-lg bg-destructive/5 px-2 py-1 text-xs text-destructive">
                    這一部分有待確認的地方
                    <IssueMarks issues={placed.top} />
                </div>
            )}
            {sections.map((section, index) => (
                <SectionView key={index} section={section} index={index} placed={placed} changed={changed}
                    keyOf={(paragraph) => changedKey(question.no, part, index, paragraph)} />
            ))}
        </div>
    );
}

function ReviseForm({ disabled, onSubmit, label }: {
    disabled: boolean; onSubmit: (instruction: string) => Promise<unknown>; label: string;
}) {
    const [open, setOpen] = useState(false);
    const [text, setText] = useState("");
    const { busy, error, run } = useRun();
    if (!open) {
        return (
            <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => setOpen(true)}>
                <Pencil aria-hidden="true" />修改這段
            </Button>
        );
    }
    return (
        <div className="flex flex-col gap-2 rounded-xl border border-border bg-card p-2.5">
            <Textarea aria-label={`${label}修改意見`} value={text} onChange={(event) => setText(event.target.value)}
                placeholder="例如：語氣更肯定，但不能說已編列預算" maxLength={500} />
            <div className="flex flex-wrap gap-2">
                <Button type="button" size="sm" disabled={busy || disabled || !text.trim()}
                    onClick={async () => {
                        if (await run(() => onSubmit(text.trim()))) {
                            setText("");
                            setOpen(false);
                        }
                    }}>
                    送出修改
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>取消</Button>
            </div>
            <p className="text-xs text-muted-foreground">只會修改{label}，其他題與其他部分維持原樣；內容仍須以資料為依據。</p>
            <ErrorLine message={error} />
        </div>
    );
}

function QuestionTabs({ document, selected, onSelect }: { document: QaDocument; selected: number; onSelect: (no: number) => void }) {
    return (
        <div role="tablist" aria-label="題目" className="flex gap-1 overflow-x-auto border-b border-border px-3">
            {document.questions.map((question) => {
                const count = issuesOf(document, question.no).length;
                const active = question.no === selected;
                return (
                    <button key={question.no} type="button" role="tab" aria-selected={active} onClick={() => onSelect(question.no)}
                        className={cn("flex shrink-0 items-center gap-1 border-b-2 px-2.5 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            active ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
                        第 {question.no} 題
                        {count > 0 && <span aria-label={`${count} 項待確認`} className="text-xs text-destructive">⚠{count}</span>}
                    </button>
                );
            })}
        </div>
    );
}

function PartToggle({ value, onChange, document, no }: {
    value: PreviewPart; onChange: (part: PreviewPart) => void; document: QaDocument; no: number;
}) {
    return (
        <div role="group" aria-label="簡答或詳答" className="inline-flex gap-1 rounded-lg bg-muted p-0.5">
            {(["short", "detail"] as const).map((part) => {
                const count = issuesOf(document, no, part).length;
                return (
                    <button key={part} type="button" aria-pressed={value === part} onClick={() => onChange(part)}
                        className={cn("rounded-md px-3 py-1 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            value === part ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
                        {PART_LABELS[part]}
                        {count > 0 && <span className="ml-1 text-xs text-destructive">⚠{count}</span>}
                    </button>
                );
            })}
        </div>
    );
}

function ChangeLegend({ baseTime, note }: { baseTime: string; note?: string | null }) {
    return (
        <p className="text-xs text-muted-foreground">
            <span className="mr-1 inline-block size-2 rounded-sm bg-primary/30 align-baseline" aria-hidden="true" />
            標示處為相較{baseTime ? ` ${baseTime} ` : "前一"}版本修改的段落{note ? `（${note}）` : ""}
        </p>
    );
}

function SourceList({ sources }: { sources: string[] }) {
    if (sources.length === 0) return null;
    return (
        <section aria-label="參考資料" className="mt-3 flex flex-col gap-1 border-t border-border pt-3 text-sm">
            <h4 className="font-semibold">參考資料</h4>
            {sources.map((name, index) => (
                <p key={index} className="break-words text-muted-foreground">[{index + 1}] {name}</p>
            ))}
        </section>
    );
}

function PreviewBody({ document, base, sessionVersionId, running, phase, onRevise, baseTime }: {
    document: QaDocument; base: QaDocument | null; sessionVersionId: string; running: boolean; phase: string | null;
    onRevise: (body: QaRevisionInput) => Promise<unknown>; baseTime: string;
}) {
    const [part, setPart] = useState<PreviewPart>("short");
    const [selected, setSelected] = useState<number | null>(null);
    // The chosen question survives a version switch; a version without it shows the first.
    const question = document.questions.find((item) => item.no === selected) ?? document.questions[0];
    if (!question) return <p className="p-4 text-sm text-muted-foreground">這個版本沒有內容。</p>;
    const partLabel = `第 ${question.no} 題${PART_LABELS[part]}`;
    return (
        <>
            <QuestionTabs document={document} selected={question.no} onSelect={setSelected} />
            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 text-[0.95rem] leading-relaxed">
                <div className="flex flex-col gap-3">
                    <p className="break-words font-semibold">{question.no}. {question.text}</p>
                    <div className="flex flex-wrap items-center gap-2">
                        <PartToggle value={part} onChange={setPart} document={document} no={question.no} />
                        {document.kind === "ai" && (
                            <ReviseForm label={partLabel} disabled={running}
                                onSubmit={(instruction) => onRevise({
                                    question_no: question.no, part, instruction, base_artifact_id: sessionVersionId,
                                })} />
                        )}
                    </div>
                    {running && <GenerationStatus phase={phase} />}
                    {base && <ChangeLegend baseTime={baseTime} note={document.revision_note} />}
                    <PartView document={document} question={question} part={part} base={base} />
                    <SourceList sources={document.sources} />
                </div>
            </div>
        </>
    );
}

/**
 * The preview beside the chat (a full-screen sheet on phones): one version of the document with its
 * structured content, a version switcher, per-question tabs, issue markers and scoped revision.
 */
export function QaPreviewPanel({ sessionId, versions, selectedId, onSelect, onClose, generation, onRevise }: {
    sessionId: string | null;
    versions: QaVersion[];
    selectedId: string;
    onSelect: (id: string) => void;
    onClose: () => void;
    generation: { running: boolean; phase: string | null };
    onRevise: (body: QaRevisionInput) => Promise<unknown>;
}) {
    const current = useQaVersion(sessionId, selectedId);
    const baseId = current.content?.document.revised_from ?? null;
    const base = useQaVersion(sessionId, baseId);
    const document = current.content?.document ?? null;
    const title = document?.title ?? versions.find((item) => item.artifact_id === selectedId)?.title ?? "文件預覽";
    const items = versions.map((version) => ({ value: version.artifact_id, label: versionLabel(version) }));
    return (
        <aside aria-label="文件預覽" data-qa-panel
            className="fixed inset-0 z-50 flex flex-col bg-background lg:sticky lg:top-0 lg:z-auto lg:h-screen lg:w-[min(46rem,46vw)] lg:shrink-0 lg:border-l lg:border-border">
            <header className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
                <h2 className="order-1 min-w-0 flex-1 basis-40 truncate text-sm font-semibold">{title}</h2>
                <Select items={items} value={selectedId} onValueChange={(value) => { if (value) onSelect(value); }}>
                    <SelectTrigger size="sm" aria-label="切換版本" className="order-3 max-w-56">
                        <SelectValue>{items.find((item) => item.value === selectedId)?.label}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                        {items.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
                    </SelectContent>
                </Select>
                <a href={artifactDownloadUrl(selectedId)} download className={cn(buttonVariants({ variant: "outline", size: "sm" }), "order-4")}>
                    <Download aria-hidden="true" />下載 Word
                </a>
                <Button type="button" variant="ghost" size="icon-sm" aria-label="關閉預覽" onClick={onClose} className="order-2 sm:order-last">
                    <X aria-hidden="true" />
                </Button>
            </header>
            {document ? (
                <PreviewBody document={document} base={base.content?.document ?? null} sessionVersionId={selectedId}
                    running={generation.running} phase={generation.phase} onRevise={onRevise}
                    baseTime={versionTime(versions.find((item) => item.artifact_id === baseId)?.created_at ?? "")} />
            ) : current.error ? (
                <div role="alert" className="flex items-center gap-2 p-4 text-sm text-destructive">
                    <CircleAlert size={16} aria-hidden="true" />{current.error}
                </div>
            ) : (
                <div role="status" className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" aria-hidden="true" />載入中…
                </div>
            )}
        </aside>
    );
}
