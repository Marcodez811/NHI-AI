"use client";

import { PageContainer, PageHeader } from "@/components/app-shell/page-layout";
import { WorkflowModelSettings } from "./WorkflowModelSettings";

/** The 立院QA writer runs from the chat skill; this page only holds its model settings. */
export function LegislativeQaView() {
    return (
        <PageContainer>
            <PageHeader title="立院QA 撰寫" description="立院QA 在對話中以「立院QA」技能產生；這裡設定撰寫（內容撰寫）與檢核（內容審查，建議用不同的模型）使用的模型。" />
            <WorkflowModelSettings workflow="legislative_qa" />
        </PageContainer>
    );
}
