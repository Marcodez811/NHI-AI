"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CircleAlert, LoaderCircle, Send } from "lucide-react";

import {
    ApiError,
    approveSlideJobOutline,
    getSlideJobOutline,
    streamSlideJobOutlineMessage,
    type OutlineEmphasis,
    type OutlineRevisionResponse,
} from "../../lib/api";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";

const EMPHASIS_OPTIONS: ReadonlyArray<{ value: OutlineEmphasis; label: string }> = [
    { value: "light", label: "輕" },
    { value: "normal", label: "一般" },
    { value: "deep", label: "深入" },
];

function errorMessage(error: unknown): string {
    return error instanceof ApiError
        ? error.message
        : "大綱服務暫時無法使用，請稍後再試。";
}

/** Lets a human shape the grounded plan before the author starts producing slides. */
export function OutlineReview({
    jobId,
    onApproved,
}: {
    jobId: string;
    onApproved?: () => void;
}) {
    const [revision, setRevision] = useState<OutlineRevisionResponse | null>(null);
    const [emphasis, setEmphasis] = useState<Record<string, OutlineEmphasis>>({});
    const [draft, setDraft] = useState("");
    const [plannerReply, setPlannerReply] = useState("");
    const [loading, setLoading] = useState(true);
    const [sending, setSending] = useState(false);
    const [approving, setApproving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const resumedApprovedRevision = useRef<string | null>(null);

    const loadRevision = async () => {
        setLoading(true);
        try {
            const next = await getSlideJobOutline(jobId);
            setRevision(next);
            setEmphasis(Object.fromEntries(next.outline.nodes.map((node) => [node.id, node.emphasis])));
            setError(null);
        } catch (loadError) {
            setError(errorMessage(loadError));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void loadRevision();
        // jobId identifies an immutable workflow; changing it must load its own revision.
    }, [jobId]);

    useEffect(() => {
        if (!revision?.approved_at) return;
        const approvalKey = `${jobId}:${revision.revision}`;
        if (resumedApprovedRevision.current === approvalKey) return;
        resumedApprovedRevision.current = approvalKey;
        onApproved?.();
    }, [jobId, onApproved, revision?.approved_at, revision?.revision]);

    const changedEmphasis = useMemo(() => revision?.outline.nodes.filter(
        (node) => emphasis[node.id] && emphasis[node.id] !== node.emphasis,
    ) ?? [], [emphasis, revision]);

    const sendMessage = async () => {
        if (!revision || sending || approving) return;
        const request = draft.trim() || (changedEmphasis.length
            ? `請調整各段落的著重程度：${changedEmphasis.map((node) => `${node.heading}改為「${EMPHASIS_OPTIONS.find((option) => option.value === emphasis[node.id])?.label}」`).join("；")}。`
            : "");
        if (!request) {
            setError("請輸入調整建議，或先變更段落著重程度。");
            return;
        }
        setSending(true);
        setError(null);
        setNotice(null);
        setPlannerReply("");
        try {
            await streamSlideJobOutlineMessage(jobId, request, {
                onDelta: (text) => setPlannerReply((reply) => reply + text),
            });
            setDraft("");
            await loadRevision();
            setNotice("已產生新的大綱版本，請確認後再核准。");
        } catch (sendError) {
            setError(errorMessage(sendError));
        } finally {
            setSending(false);
        }
    };

    const approve = async () => {
        if (!revision || approving || sending) return;
        const displayedRevision = revision.revision;
        setApproving(true);
        setError(null);
        setNotice(null);
        try {
            await approveSlideJobOutline(jobId, displayedRevision);
            setNotice(`已核准第 ${displayedRevision} 版，正在開始撰寫簡報。`);
            onApproved?.();
        } catch (approveError) {
            if (approveError instanceof ApiError && approveError.status === 409) {
                await loadRevision();
                setNotice("大綱已有新版本，已重新載入；請檢閱後再核准。系統未自動核准新版。");
            } else {
                setError(errorMessage(approveError));
            }
        } finally {
            setApproving(false);
        }
    };

    if (loading && !revision) {
        return <div role="status" className="flex items-center gap-2 rounded-lg border border-border bg-secondary/40 p-4 text-sm text-muted-foreground"><LoaderCircle className="size-4 animate-spin" />正在載入簡報大綱…</div>;
    }

    if (!revision) {
        return <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error || "目前找不到可檢閱的大綱。"}<Button type="button" variant="link" size="sm" className="ml-2" onClick={() => void loadRevision()}>重新載入</Button></div>;
    }

    const locked = revision.approved_at !== null;
    return (
        <section aria-labelledby="outline-review-title" className="rounded-xl border border-primary/20 bg-primary/[.025] p-4 sm:p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 id="outline-review-title" className="font-semibold">檢閱簡報大綱</h2>
                    <p className="mt-1 text-sm text-muted-foreground">第 {revision.revision} 版 · 核准後才會開始撰寫簡報。</p>
                </div>
                <span className="rounded-full bg-secondary px-2.5 py-1 text-xs text-muted-foreground">約 {revision.outline.total_slides} 頁</span>
            </div>
            {notice && <p role="status" className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{notice}</p>}
            {error && <p role="alert" className="mt-4 flex gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"><CircleAlert className="mt-0.5 size-4 shrink-0" />{error}</p>}
            <div className="mt-5 rounded-lg border border-border bg-background p-3">
                <p className="text-xs font-medium text-muted-foreground">敘事主軸</p>
                <p className="mt-1 text-sm leading-6">{revision.outline.narrative}</p>
            </div>
            <ol className="mt-4 space-y-3">
                {revision.outline.nodes.map((node, index) => (
                    <li key={node.id} className="rounded-lg border border-border bg-background p-3">
                        <div className="flex flex-col justify-between gap-3 sm:flex-row">
                            <div className="min-w-0">
                                <p className="text-sm font-medium">{index + 1}. {node.heading}</p>
                                <p className="mt-1 text-sm text-muted-foreground">{node.intent}</p>
                            </div>
                            <fieldset disabled={locked || sending || approving} className="flex shrink-0 items-center gap-1" aria-label={`${node.heading}的著重程度`}>
                                <legend className="sr-only">著重程度</legend>
                                {EMPHASIS_OPTIONS.map((option) => <Button key={option.value} type="button" size="xs" variant={emphasis[node.id] === option.value ? "default" : "outline"} aria-pressed={emphasis[node.id] === option.value} onClick={() => setEmphasis((current) => ({ ...current, [node.id]: option.value }))}>{option.label}</Button>)}
                            </fieldset>
                        </div>
                        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">{node.key_points.map((point) => <li key={point}>{point}</li>)}</ul>
                    </li>
                ))}
            </ol>
            {plannerReply && <p className="mt-4 rounded-lg bg-secondary/60 p-3 text-sm leading-6">{plannerReply}</p>}
            {!locked && <div className="mt-5 border-t border-border pt-4">
                <label htmlFor="outline-message" className="text-sm font-medium">告訴規劃助手如何調整</label>
                <Textarea id="outline-message" value={draft} onChange={(event) => setDraft(event.target.value)} disabled={sending || approving} maxLength={4000} rows={3} placeholder="例如：請加強改革對民眾的影響，並縮短背景說明。" className="mt-2" />
                <div className="mt-3 flex flex-wrap justify-between gap-2">
                    <p className="text-xs text-muted-foreground">變更著重程度後，按「產生新版本」才會套用。</p>
                    <div className="flex gap-2"><Button type="button" variant="outline" onClick={() => void sendMessage()} disabled={sending || approving || (!draft.trim() && !changedEmphasis.length)}>{sending ? <LoaderCircle className="animate-spin" /> : <Send />}產生新版本</Button><Button type="button" onClick={() => void approve()} disabled={sending || approving}>{approving && <LoaderCircle className="animate-spin" />}核准第 {revision.revision} 版</Button></div>
                </div>
            </div>}
        </section>
    );
}
