import type { DocumentRead } from "../../lib/api/slides";
import { SUPPORTED_SLIDE_EXTENSIONS } from "../../lib/api/slides";
import type { AgentJobClientPhase } from "../../lib/hooks/useAgentJob";
import { extensionOf } from "./WorkspaceViewUtils";

function supportsSlides(document: DocumentRead): boolean {
    return (SUPPORTED_SLIDE_EXTENSIONS as readonly string[]).includes(extensionOf(document));
}

export function analyzeSlideReadiness({
    docs,
    selected,
    title,
    phase,
    jobStatus,
}: {
    docs: DocumentRead[];
    selected: string[];
    title: string;
    phase: AgentJobClientPhase;
    jobStatus?: string | null;
}) {
    const selectedDocs = docs.filter((document) => selected.includes(document.id));
    const eligible = selectedDocs.filter(
        (document) => document.status === "ready" && supportsSlides(document),
    );
    const unsupported = selectedDocs.filter(
        (document) => document.status === "ready" && !supportsSlides(document),
    );
    const notReady = selectedDocs.filter((document) => document.status !== "ready");
    const availableSources = docs.filter(
        (document) => document.status === "ready" && supportsSlides(document),
    );
    const busy = phase === "submitting" || phase === "polling";
    const canGenerate =
        !busy &&
        Boolean(title.trim()) &&
        selectedDocs.length > 0 &&
        notReady.length === 0 &&
        unsupported.length === 0;
    const readinessMessage = describeReadiness({
        busy,
        canGenerate,
        phase,
        title,
        selectedDocs,
        notReady,
        unsupported,
        eligibleCount: eligible.length,
        jobStatus,
    });
    const availabilityMessage = describeAvailability(docs, availableSources.length);

    return {
        selectedDocs,
        eligible,
        unsupported,
        notReady,
        availableSources,
        busy,
        canGenerate,
        readinessMessage,
        availabilityMessage,
    };
}

function describeReadiness({
    busy,
    canGenerate,
    phase,
    title,
    selectedDocs,
    notReady,
    unsupported,
    eligibleCount,
    jobStatus,
}: {
    busy: boolean;
    canGenerate: boolean;
    phase: AgentJobClientPhase;
    title: string;
    selectedDocs: DocumentRead[];
    notReady: DocumentRead[];
    unsupported: DocumentRead[];
    eligibleCount: number;
    jobStatus?: string | null;
}): string {
    if (busy) return "正在建立簡報，請稍候。";
    if (canGenerate) return "來源已就緒，可以生成。";
    if (phase === "expired") return "這次簡報工作已過期，請重新生成。";
    if (!title.trim()) return "請先輸入簡報標題。";
    if (!selectedDocs.length) return "選取至少一份可用來源後即可生成。";
    if (notReady.some((document) => document.status === "failed")) {
        const count = notReady.filter((document) => document.status === "failed").length;
        return `${count} 份選取文件索引失敗，請在知識庫重新上傳或移除。`;
    }
    if (notReady.some((document) => ["deleting", "delete_failed"].includes(document.status))) {
        const count = notReady.filter((document) =>
            ["deleting", "delete_failed"].includes(document.status),
        ).length;
        return `${count} 份選取文件正在刪除，請移除後再生成。`;
    }
    if (notReady.length) return `${notReady.length} 份選取文件正在索引，完成後才能生成。`;
    if (unsupported.length) return "請移除不支援的來源格式後再生成。";
    if (selectedDocs.length > eligibleCount) return "請確認所有選取來源都已就緒。";
    if (jobStatus === "failed") return "這次簡報生成失敗，請選擇再次生成。";
    return "來源已就緒，可以生成。";
}

function describeAvailability(docs: DocumentRead[], availableCount: number): string {
    if (!docs.length) return "知識庫目前沒有文件。";
    if (availableCount) return `知識庫目前有 ${availableCount} 份可用來源。`;
    if (docs.some((document) => ["queued", "indexing"].includes(document.status))) {
        return "來源正在索引，完成後會出現在可選清單。";
    }
    return "知識庫中的文件尚未完成索引，或格式尚不支援。";
}
