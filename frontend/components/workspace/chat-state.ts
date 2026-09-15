import type { RetrievalStatus } from "../../lib/api/retrieval";
import type { ChatEmptyStateKind } from "./ChatEmptyState";

export function resolveChatEmptyState({
    ownsRetrievalState,
    retrievalStatus,
    retrievalLoading,
    retrievalError,
    catalogLoading,
    hasPendingDocuments,
    hasAnyReadyDocuments,
    hasCategoryReadyDocuments,
}: {
    ownsRetrievalState: boolean;
    retrievalStatus?: RetrievalStatus | null;
    retrievalLoading: boolean;
    retrievalError?: string | null;
    catalogLoading: boolean;
    hasPendingDocuments: boolean;
    hasAnyReadyDocuments: boolean;
    hasCategoryReadyDocuments: boolean;
}): ChatEmptyStateKind | null {
    if (!ownsRetrievalState) return null;
    if (retrievalError) return "error";
    if (
        retrievalLoading ||
        catalogLoading ||
        hasPendingDocuments ||
        !retrievalStatus ||
        retrievalStatus.state === "uninitialized" ||
        retrievalStatus.state === "provisioning"
    ) {
        return "loading";
    }
    if (retrievalStatus.state === "error" || !retrievalStatus.can_retrieve) {
        return "error";
    }
    if (!hasAnyReadyDocuments) return "empty";
    if (!hasCategoryReadyDocuments) return "category-empty";
    return null;
}

export function isChatComposerBlocked(kind: ChatEmptyStateKind | null): boolean {
    return kind !== null;
}
