/**
 * Compatibility entry point for existing workspace consumers.
 *
 * Domain-focused components live beside this file. New imports may target
 * those files directly; existing imports from `WorkspaceViews` remain valid.
 */
export { ChatView } from "./ChatView";
export {
    FilesView,
    FolderManager,
    IngestionDetails,
} from "./FilesView";
export { Sidebar } from "./Sidebar";
export { SlidesView } from "./SlidesView";
export { UploadModal } from "./UploadModal";
export {
    categoryLabel,
    documentDisplayName,
    extensionOf,
    formatBytes,
    formatDate,
    formatDateTime,
    statusLabel,
} from "./WorkspaceViewUtils";
export type { ChatMessage, Tone, View } from "../../lib/workspace/types";
