"use client";

import { BarChart3, ChevronRight, ClipboardList, Newspaper, Presentation } from "lucide-react";
import Link from "next/link";

const workflows = [
    {
        href: "/slides",
        label: "簡報生成",
        description: "根據來源文件產生完整 PowerPoint 簡報",
        icon: Presentation,
        available: true,
    },
    {
        href: "/news",
        label: "新聞稿生成",
        description: "根據來源文件整理並撰寫新聞稿",
        icon: Newspaper,
        available: true,
    },
    {
        label: "政策摘要",
        description: "將多份文件整理成結構化摘要",
        icon: ClipboardList,
        available: false,
    },
] as const;

export function WorkflowList() {
    return (
        <section className="px-5 py-8 lg:px-10 lg:py-12">
            <div className="mx-auto max-w-2xl">
                <div className="mb-8">
                    <h1 className="text-[24px] font-semibold tracking-tight">AI 工作流</h1>
                    <p className="mt-2 text-sm text-muted-foreground">選擇工作流，以知識庫文件產生結構化輸出。</p>
                </div>
                <div className="divide-y divide-border border-y border-border">
                    {workflows.map((workflow) => {
                        const { label, description, icon: Icon, available } = workflow;
                        const href = "href" in workflow ? workflow.href : undefined;
                        const content = (
                            <div className={`flex items-center gap-4 px-2 py-5 transition-colors ${available ? "hover:bg-accent/60" : "opacity-65"}`}>
                                <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary" aria-hidden="true">
                                    <Icon className="size-[18px]" />
                                </span>
                                <span className="min-w-0 flex-1">
                                    <span className="flex items-center gap-2 text-sm font-semibold">
                                        {label}
                                        {!available && <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-normal text-muted-foreground">即將推出</span>}
                                    </span>
                                    <span className="mt-1 block text-sm text-muted-foreground">{description}</span>
                                </span>
                                {available ? <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /> : <BarChart3 className="size-4 shrink-0 text-muted-foreground/50" aria-hidden="true" />}
                            </div>
                        );
                        return available && href ? <Link href={href} key={label}>{content}</Link> : <div key={label} aria-disabled="true">{content}</div>;
                    })}
                </div>
            </div>
        </section>
    );
}
