"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { CircleAlert, LoaderCircle } from "lucide-react";

import type { DocumentRead } from "../../lib/api/slides";
import { slideDownloadUrl } from "../../lib/api/slides";
import { useSlideJob } from "../../lib/hooks/useSlideJob";
import { Button, buttonVariants } from "../ui/button";
import { AgentJobActivity } from "./AgentJobActivity";
import { OutlineReview } from "./OutlineReview";
import { SlideCompletedResult } from "./SlideCompletedResult";

/** A job URL owns this entire lifecycle; browser storage never selects the job. */
export function SlideJobView({
    jobId,
    docs,
}: {
    jobId: string;
    docs: DocumentRead[];
}) {
    const router = useRouter();
    const slideJob = useSlideJob({ jobId });
    const job = slideJob.job?.job_id === jobId ? slideJob.job : null;
    const awaitingOutline = job?.status === "awaiting_input" && job.phase === "awaiting_outline";
    const downloadUrl = job ? slideDownloadUrl(job) : null;

    if (slideJob.phase === "expired") {
        return (
            <section className="px-5 py-10 lg:px-10" aria-labelledby="slide-job-not-found-title">
                <div className="mx-auto max-w-[720px] rounded-xl border border-border bg-card p-6">
                    <CircleAlert className="text-muted-foreground" size={22} aria-hidden="true" />
                    <h1 id="slide-job-not-found-title" className="mt-4 text-xl font-semibold">找不到簡報工作</h1>
                    <p className="mt-2 text-sm text-muted-foreground">這個工作可能已移除，或連結有誤。請返回簡報列表選擇其他工作。</p>
                    <Link href="/slides" className={buttonVariants({ variant: "outline", className: "mt-5" })}>
                        返回簡報列表
                    </Link>
                </div>
            </section>
        );
    }

    if (job?.status === "completed" && downloadUrl) {
        return (
            <SlideCompletedResult
                job={job}
                docs={docs}
                title={job.brief?.title ?? "簡報"}
                downloadUrl={downloadUrl}
                onNewPresentation={() => router.push("/slides")}
            />
        );
    }

    return (
        <section className="px-5 py-8 lg:px-10 lg:py-10" aria-labelledby="slide-job-title">
            <div className="mx-auto max-w-[720px]">
                <Link href="/slides" className="mb-5 inline-flex items-center text-xs text-info hover:text-info/80">
                    ← 返回簡報列表
                </Link>
                <div className="mb-7">
                    <h1 id="slide-job-title" className="text-[24px] font-semibold tracking-tight">
                        {job?.brief?.title ?? "簡報工作"}
                    </h1>
                    <p className="mt-1 text-sm text-muted-foreground">
                        {awaitingOutline
                            ? "請檢閱大綱並核准，簡報才會繼續生成。"
                            : job?.status === "failed"
                              ? "這次生成未完成；您可以返回列表重新建立簡報。"
                              : "工作會在背景繼續執行，您可以稍後從簡報列表回到這裡。"}
                    </p>
                </div>

                {awaitingOutline && job ? (
                    <OutlineReview jobId={jobId} onApproved={slideJob.resumePolling} />
                ) : job ? (
                    <div className="space-y-4">
                        <AgentJobActivity
                            job={job}
                            clientPhase={slideJob.phase}
                            phaseHistory={slideJob.phaseHistory}
                            warning={slideJob.warning}
                            error={slideJob.error?.message}
                            workflowLabel="簡報"
                            onRefresh={() => void slideJob.pollNow()}
                        />
                        {job.status === "failed" && (
                            <Link href="/slides" className={buttonVariants({ variant: "outline" })}>
                                返回簡報列表
                            </Link>
                        )}
                    </div>
                ) : (
                    <div className="flex items-center gap-3 rounded-xl border border-border bg-card px-5 py-6 text-sm text-muted-foreground" role="status">
                        <LoaderCircle size={18} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                        {slideJob.warning ?? "正在載入簡報工作…"}
                        {slideJob.warning && (
                            <Button type="button" variant="link" size="sm" onClick={() => void slideJob.pollNow()}>
                                立即重試
                            </Button>
                        )}
                    </div>
                )}
            </div>
        </section>
    );
}
