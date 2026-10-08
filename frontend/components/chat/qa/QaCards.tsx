"use client";

import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { Check, ChevronDown, CircleAlert, FileText, Plus, Trash2, Upload } from "lucide-react";
import { ApiError } from "../../../lib/api/client";
import { QA_EVIDENCE_GROUPS } from "../../../lib/api/qa";
import type {
    QaCard,
    QaDocumentsCardData,
    QaEvidenceCardData,
    QaEvidenceGroup,
    QaOutline,
    QaOutlineCardData,
    QaQuestion,
    QaQuestionsCardData,
    QaWorkspace,
} from "../../../lib/api/qa";
import type { useQaSkill } from "../../../lib/hooks/useQaSkill";
import { Badge } from "../../ui/badge";
import { Button } from "../../ui/button";
import { Checkbox } from "../../ui/checkbox";
import { Input } from "../../ui/input";
import { Textarea } from "../../ui/textarea";

export type QaController = ReturnType<typeof useQaSkill>;

const GROUP_LABELS: Record<QaEvidenceGroup, string> = {
    figures: "數據",
    aim: "政策目的",
    status: "辦理現況",
    dispute: "爭議",
    next_steps: "後續工作",
};

const TITLES: Record<QaCard["kind"], string> = {
    questions: "題目清單",
    documents: "參考資料",
    evidence: "資訊整理",
    outline: "答題架構",
};

/** Runs a card action, keeping its busy flag and the server's inline error message. */
function useRun() {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const run = async (action: () => Promise<unknown>) => {
        setBusy(true);
        setError(null);
        try {
            await action();
            return true;
        } catch (caught) {
            setError(caught instanceof ApiError ? caught.message : "操作失敗，請稍後再試。");
            return false;
        } finally {
            setBusy(false);
        }
    };
    return { busy, error, run };
}

function ErrorLine({ message }: { message: string | null }) {
    if (!message) return null;
    return (
        <div role="alert" className="flex items-center gap-1.5 text-xs text-destructive">
            <CircleAlert size={13} aria-hidden="true" />{message}
        </div>
    );
}

function ConfirmedBadge() {
    return <Badge variant="secondary" className="shrink-0"><Check size={11} aria-hidden="true" />已確認</Badge>;
}

/** Older snapshots collapse to one line; the newest card of a kind is open and actionable. */
function CardShell({ kind, readOnly, badge, children }: {
    kind: QaCard["kind"];
    readOnly: boolean;
    badge?: ReactNode;
    children: ReactNode;
}) {
    const title = TITLES[kind];
    if (readOnly) {
        return (
            <details data-qa-card={kind} data-qa-readonly="true" className="group mt-3 rounded-xl border border-border/60 bg-muted/30 px-3 py-2 text-sm">
                <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs text-muted-foreground [&::-webkit-details-marker]:hidden">
                    <ChevronDown size={13} className="transition-transform group-open:rotate-180" aria-hidden="true" />
                    {title}（較早版本）
                </summary>
                <div className="mt-2 flex flex-col gap-2">{children}</div>
            </details>
        );
    }
    return (
        <section data-qa-card={kind} aria-label={title} className="mt-3 flex min-w-0 scroll-mt-14 flex-col gap-3 rounded-2xl border border-border bg-card p-3 text-sm sm:p-4">
            <div className="flex items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">{title}</h3>
                {badge}
            </div>
            {children}
        </section>
    );
}

// ---------------------------------------------------------------- questions

function QuestionsCard({ data, live, readOnly, qa }: {
    data: QaQuestionsCardData; live: QaWorkspace | null; readOnly: boolean; qa: QaController;
}) {
    const source = !readOnly && live ? live.questions : data;
    const confirmed = Boolean(source.confirmed);
    const items = source.items ?? [];
    const [editing, setEditing] = useState<QaQuestion[] | null>(null);
    const { busy, error, run } = useRun();
    const payload = (rows: QaQuestion[]) => rows.map((row) => ({ no: row.no, text: row.text, note: row.note }));
    const update = (index: number, patch: Partial<QaQuestion>) =>
        setEditing((rows) => rows?.map((row, i) => (i === index ? { ...row, ...patch } : row)) ?? rows);

    return (
        <CardShell kind="questions" readOnly={readOnly} badge={confirmed ? <ConfirmedBadge /> : undefined}>
            {editing ? (
                <div className="flex flex-col gap-2">
                    {editing.map((row, index) => (
                        <div key={index} className="flex flex-col gap-1.5 rounded-lg border border-border/60 p-2">
                            <div className="flex items-center gap-2">
                                <span className="shrink-0 text-xs text-muted-foreground">第 {index + 1} 題</span>
                                <Input aria-label={`第 ${index + 1} 題題目`} value={row.text}
                                    onChange={(event) => update(index, { text: event.target.value })} />
                                <Button type="button" variant="ghost" size="icon-sm" aria-label={`刪除第 ${index + 1} 題`}
                                    onClick={() => setEditing((rows) => rows?.filter((_, i) => i !== index) ?? rows)}>
                                    <Trash2 />
                                </Button>
                            </div>
                            <Input aria-label={`第 ${index + 1} 題備註`} placeholder="備註（選填）" value={row.note ?? ""}
                                onChange={(event) => update(index, { note: event.target.value })} />
                        </div>
                    ))}
                    <div className="flex flex-wrap gap-2">
                        <Button type="button" variant="outline" size="sm"
                            onClick={() => setEditing((rows) => [...(rows ?? []), { no: 0, text: "", note: "" }])}>
                            <Plus /> 新增題目
                        </Button>
                        <Button type="button" size="sm" disabled={busy || !editing.some((row) => row.text.trim())}
                            onClick={async () => {
                                const rows = editing.filter((row) => row.text.trim());
                                const ok = await run(() => qa.saveQuestions(rows.map((row) => ({
                                    no: row.no || undefined, text: row.text.trim(), note: row.note?.trim() || undefined,
                                })), false));
                                if (ok) setEditing(null);
                            }}>
                            儲存
                        </Button>
                        <Button type="button" variant="ghost" size="sm" onClick={() => setEditing(null)}>取消</Button>
                    </div>
                </div>
            ) : (
                <ol className="flex list-none flex-col gap-2">
                    {items.map((item) => (
                        <li key={item.no} className="flex gap-2">
                            <span className="shrink-0 font-medium tabular-nums text-muted-foreground">{item.no}.</span>
                            <div className="min-w-0 break-words">
                                <div>{item.text}</div>
                                {item.note && <div className="text-xs text-muted-foreground">{item.note}</div>}
                            </div>
                        </li>
                    ))}
                </ol>
            )}
            {!readOnly && !editing && (
                <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" disabled={busy || confirmed || !items.length}
                        onClick={() => void run(() => qa.saveQuestions(payload(items), true))}>
                        確認
                    </Button>
                    <Button type="button" variant="outline" size="sm" disabled={busy}
                        onClick={() => setEditing(items.map((item) => ({ ...item })))}>
                        編輯
                    </Button>
                </div>
            )}
            <ErrorLine message={error} />
        </CardShell>
    );
}

// ---------------------------------------------------------------- documents

function DocumentsCard({ data, live, readOnly, qa, onUploadFiles }: {
    data: QaDocumentsCardData; live: QaWorkspace | null; readOnly: boolean; qa: QaController;
    onUploadFiles?: (files: File[]) => Promise<unknown> | void;
}) {
    const confirmed = !readOnly && live ? live.documents.confirmed : Boolean(data.confirmed);
    const attached = new Set(qa.sessionAttachments.map((item) => item.id));
    const fileInput = useRef<HTMLInputElement>(null);
    const { busy, error, run } = useRun();
    const candidateIds = new Set(data.items.flatMap((item) => item.candidates.map((c) => c.id)));
    const uploads = qa.sessionAttachments.filter((item) => !candidateIds.has(item.id));

    return (
        <CardShell kind="documents" readOnly={readOnly} badge={confirmed ? <ConfirmedBadge /> : undefined}>
            {data.items.map((item) => (
                <div key={item.question_no} className="flex flex-col gap-1.5">
                    <div className="text-xs font-medium text-muted-foreground">第 {item.question_no} 題　{item.question_text}</div>
                    {item.candidates.length === 0 && <div className="text-xs text-muted-foreground">沒有找到相關資料。</div>}
                    {item.candidates.map((candidate) => {
                        const checked = readOnly ? candidate.attached : attached.has(candidate.id);
                        return (
                            <div key={candidate.id} className="flex items-start gap-2 rounded-lg border border-border/60 p-2">
                                <Checkbox aria-label={candidate.name} checked={checked}
                                    disabled={readOnly || confirmed || busy}
                                    onCheckedChange={(next) => void run(() => qa.toggleDocument(candidate.id, Boolean(next)))}
                                    className="mt-0.5" />
                                <span className="min-w-0">
                                    <span className="block break-words font-medium">{candidate.name}</span>
                                    {candidate.snippet && (
                                        <span className="line-clamp-2 break-words text-xs text-muted-foreground">{candidate.snippet}</span>
                                    )}
                                </span>
                            </div>
                        );
                    })}
                </div>
            ))}
            {!readOnly && uploads.length > 0 && (
                <div className="flex flex-wrap gap-1.5" aria-label="已加入的檔案">
                    {uploads.map((item) => (
                        <span key={item.id} className="inline-flex max-w-full items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs">
                            <FileText size={13} className="shrink-0 text-muted-foreground" aria-hidden="true" />
                            <span className="truncate">{item.display_name}</span>
                        </span>
                    ))}
                </div>
            )}
            {!readOnly && (
                <div className="flex flex-wrap gap-2">
                    <Button type="button" variant="outline" size="sm" disabled={confirmed || busy || !onUploadFiles}
                        onClick={() => fileInput.current?.click()}>
                        <Upload /> 上傳檔案
                    </Button>
                    <input ref={fileInput} type="file" multiple hidden accept=".pdf,.docx,.txt,.md"
                        data-testid="qa-upload-input"
                        onChange={(event) => {
                            const files = Array.from(event.target.files || []);
                            if (files.length) void run(async () => { await onUploadFiles?.(files); });
                            event.target.value = "";
                        }} />
                    <Button type="button" size="sm" disabled={busy || confirmed || qa.sessionAttachments.length === 0}
                        onClick={() => void run(() => qa.confirmDocuments())}>
                        確認使用這些資料
                    </Button>
                </div>
            )}
            <ErrorLine message={error} />
        </CardShell>
    );
}

// ---------------------------------------------------------------- evidence

function EvidenceCard({ data, readOnly }: { data: QaEvidenceCardData; readOnly: boolean }) {
    return (
        <CardShell kind="evidence" readOnly={readOnly}>
            <details open={!readOnly} className="group">
                <summary className="flex cursor-pointer list-none items-center gap-1.5 font-medium [&::-webkit-details-marker]:hidden">
                    <ChevronDown size={14} className="shrink-0 transition-transform group-open:rotate-180" aria-hidden="true" />
                    <span className="min-w-0 break-words">第 {data.question_no} 題　{data.question_text}</span>
                </summary>
                <div className="mt-2 flex flex-col gap-3">
                    {QA_EVIDENCE_GROUPS.map((group) => {
                        const entries = data.evidence?.[group] ?? [];
                        return (
                            <div key={group} className="flex flex-col gap-1.5">
                                <div className="text-xs font-semibold text-muted-foreground">{GROUP_LABELS[group]}</div>
                                {entries.length === 0 && <div className="text-xs text-muted-foreground">無</div>}
                                {entries.map((entry, index) => (
                                    <div key={index} className="flex flex-col gap-1 rounded-lg bg-muted/40 p-2">
                                        <div className="break-words">{entry.text}</div>
                                        <div className="flex min-w-0 flex-col gap-1">
                                            <span className="inline-flex max-w-full items-center gap-1 self-start rounded-md border border-border bg-card px-1.5 py-0.5 text-xs">
                                                <FileText size={12} className="shrink-0 text-muted-foreground" aria-hidden="true" />
                                                <span className="truncate">{entry.source.name}</span>
                                            </span>
                                            {entry.quote && (
                                                <details className="text-xs text-muted-foreground">
                                                    <summary className="cursor-pointer">查看原文</summary>
                                                    <p className="mt-1 break-words">{entry.quote}</p>
                                                </details>
                                            )}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        );
                    })}
                </div>
            </details>
        </CardShell>
    );
}

// ---------------------------------------------------------------- outline

function OutlineView({ outline }: { outline: QaOutline }) {
    return (
        <div className="flex flex-col gap-3">
            <div>
                <div className="mb-1 text-xs font-semibold text-muted-foreground">簡答要點</div>
                <ul className="list-disc space-y-1 pl-5">
                    {outline.short.map((point, index) => <li key={index} className="break-words">{point}</li>)}
                </ul>
            </div>
            <div>
                <div className="mb-1 text-xs font-semibold text-muted-foreground">詳答</div>
                <div className="flex flex-col gap-2">
                    {outline.detail.map((section, index) => (
                        <div key={index}>
                            <div className="font-medium">{section.title}</div>
                            <ul className="list-disc space-y-0.5 pl-5">
                                {section.points.map((point, i) => <li key={i} className="break-words">{point}</li>)}
                            </ul>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}

const lines = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean);

function OutlineEditor({ no, outline, qa, onDone }: {
    no: number; outline: QaOutline; qa: QaController; onDone: () => void;
}) {
    const [short, setShort] = useState(outline.short.join("\n"));
    const [dispute, setDispute] = useState(outline.dispute_requested);
    const [sections, setSections] = useState(outline.detail.map((s) => ({ title: s.title, points: s.points.join("\n") })));
    const { busy, error, run } = useRun();
    const patch = (index: number, value: Partial<{ title: string; points: string }>) =>
        setSections((rows) => rows.map((row, i) => (i === index ? { ...row, ...value } : row)));
    return (
        <div className="flex flex-col gap-2">
            <label className="flex flex-col gap-1 text-xs font-semibold text-muted-foreground">
                簡答要點（一行一點）
                <Textarea value={short} rows={4} onChange={(event) => setShort(event.target.value)}
                    className="text-sm font-normal text-foreground" />
            </label>
            <div className="flex items-center gap-2 text-sm">
                <Checkbox aria-label="加入爭議點" checked={dispute} onCheckedChange={(next) => setDispute(Boolean(next))} />
                <span aria-hidden="true" onClick={() => setDispute((value) => !value)} className="cursor-pointer">加入爭議點</span>
            </div>
            {sections.map((section, index) => (
                <div key={index} className="flex flex-col gap-1.5 rounded-lg border border-border/60 p-2">
                    <div className="flex items-center gap-2">
                        <Input aria-label={`詳答段落 ${index + 1} 標題`} value={section.title}
                            onChange={(event) => patch(index, { title: event.target.value })} />
                        <Button type="button" variant="ghost" size="icon-sm" aria-label={`刪除段落 ${index + 1}`}
                            onClick={() => setSections((rows) => rows.filter((_, i) => i !== index))}>
                            <Trash2 />
                        </Button>
                    </div>
                    <Textarea aria-label={`詳答段落 ${index + 1} 要點`} value={section.points} rows={3}
                        onChange={(event) => patch(index, { points: event.target.value })} className="text-sm" />
                </div>
            ))}
            <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" size="sm"
                    onClick={() => setSections((rows) => [...rows, { title: "", points: "" }])}>
                    <Plus /> 新增段落
                </Button>
                <Button type="button" size="sm" disabled={busy || lines(short).length === 0}
                    onClick={async () => {
                        const ok = await run(() => qa.saveOutline(no, {
                            short: lines(short),
                            detail: sections.filter((s) => s.title.trim()).map((s) => ({ title: s.title.trim(), points: lines(s.points) })),
                            dispute_requested: dispute,
                            confirm: false,
                        }));
                        if (ok) onDone();
                    }}>
                    儲存
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={onDone}>取消</Button>
            </div>
            <ErrorLine message={error} />
        </div>
    );
}

function OutlineCard({ data, live, readOnly, qa, showConfirmAll }: {
    data: QaOutlineCardData; live: QaWorkspace | null; readOnly: boolean; qa: QaController; showConfirmAll: boolean;
}) {
    const no = data.question_no;
    const outline = (!readOnly && live?.outline?.[String(no)]) || data.outline;
    const [editing, setEditing] = useState(false);
    const { busy, error, run } = useRun();
    const allHave = Boolean(live) && live!.questions.items.length > 0
        && live!.questions.items.every((q) => live!.outline?.[String(q.no)]);
    const allConfirmed = allHave && live!.questions.items.every((q) => live!.outline[String(q.no)].confirmed);
    return (
        <CardShell kind="outline" readOnly={readOnly} badge={outline.confirmed ? <ConfirmedBadge /> : undefined}>
            <div className="break-words text-xs font-medium text-muted-foreground">第 {no} 題　{data.question_text}</div>
            {editing ? (
                <OutlineEditor no={no} outline={outline} qa={qa} onDone={() => setEditing(false)} />
            ) : (
                <OutlineView outline={outline} />
            )}
            {!readOnly && !editing && (
                <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" disabled={busy || outline.confirmed}
                        onClick={() => void run(() => qa.confirmOutline(no))}>
                        確認
                    </Button>
                    <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => setEditing(true)}>修改</Button>
                    {showConfirmAll && allHave && !allConfirmed && (
                        <Button type="button" variant="secondary" size="sm" disabled={busy}
                            onClick={() => void run(() => qa.confirmAllOutlines())}>
                            全部確認
                        </Button>
                    )}
                </div>
            )}
            {!readOnly && showConfirmAll && live?.stage === "ready" && (
                <p className="text-xs text-muted-foreground">大綱已全部確認，產製功能即將開放。</p>
            )}
            <ErrorLine message={error} />
        </CardShell>
    );
}

// ---------------------------------------------------------------- dispatch

export function QaCardView({ card, live, readOnly, showConfirmAll, qa, onUploadFiles }: {
    card: QaCard;
    live: QaWorkspace | null;
    readOnly: boolean;
    showConfirmAll: boolean;
    qa: QaController;
    onUploadFiles?: (files: File[]) => Promise<unknown> | void;
}) {
    switch (card.kind) {
        case "questions":
            return <QuestionsCard data={card.data as unknown as QaQuestionsCardData} live={live} readOnly={readOnly} qa={qa} />;
        case "documents":
            return <DocumentsCard data={card.data as unknown as QaDocumentsCardData} live={live} readOnly={readOnly} qa={qa} onUploadFiles={onUploadFiles} />;
        case "evidence":
            return <EvidenceCard data={card.data as unknown as QaEvidenceCardData} readOnly={readOnly} />;
        case "outline":
            return <OutlineCard data={card.data as unknown as QaOutlineCardData} live={live} readOnly={readOnly} qa={qa} showConfirmAll={showConfirmAll} />;
    }
}

/** Key identifying "the same card" across turns: kind, plus the question for per-question cards. */
export function qaCardKey(card: QaCard): string {
    const no = card.kind === "evidence" || card.kind === "outline" ? String(card.data.question_no ?? "") : "";
    return `${card.kind}:${no}`;
}
