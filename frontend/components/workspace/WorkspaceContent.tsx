"use client";

import { FilesView } from "./FilesView";
import { SlidesView } from "./SlidesView";
import { SlideJobView } from "./SlideJobView";
import { NewsView } from "./NewsView";
import { ConfirmDialog } from "../ui/confirm-dialog";
import { UploadModal } from "./UploadModal";
import { WorkflowList } from "./WorkflowList";
import type { WorkspaceController } from "../../lib/hooks/useWorkspaceController";
import type { View } from "../../lib/workspace/types";

export function WorkspaceContent({
    workspace,
    view,
    onBrowseSources,
    slideJobId,
    onSlideStart,
}: {
    workspace: WorkspaceController;
    view: View;
    onBrowseSources?: () => void;
    slideJobId?: string | null;
    onSlideStart?: () => Promise<void>;
}) {
    const catalogError = workspace.catalog.error?.message || null;

    return (
        <>
            <ConfirmDialog
                open={workspace.pendingDelete !== null}
                title={`刪除「${workspace.pendingDelete?.display_name ?? ""}」？`}
                description="此操作會移除來源與檢索資料。"
                onCancel={workspace.cancelDeleteDocument}
                onConfirm={workspace.confirmDeleteDocument}
            />
            {workspace.actionError && (
                <div
                    className="mx-auto mt-4 max-w-5xl rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
                    role="alert"
                >
                    {workspace.actionError}
                </div>
            )}
            {view === "files" && (
                <FilesView
                    docs={workspace.catalog.documents}
                    folders={workspace.catalog.folders}
                    folderId={workspace.folderId}
                    setFolderId={workspace.setFolderId}
                    folderName={workspace.folderName}
                    query={workspace.query}
                    setQuery={workspace.setQuery}
                    onUpload={() => workspace.setUploadOpen(true)}
                    onUpdate={workspace.mutateDocument}
                    onDelete={workspace.deleteDocument}
                    onDownload={workspace.downloadDocument}
                    onCreateFolder={workspace.createFolder}
                    onRenameFolder={workspace.renameFolder}
                    onDeleteFolder={workspace.deleteFolder}
                    loading={workspace.catalog.loading}
                    error={catalogError}
                    actionError={workspace.actionError}
                    getIngestion={workspace.catalog.getIngestion}
                    modes={workspace.modes}
                />
            )}
            {view === "slides" && slideJobId && (
                <SlideJobView key={slideJobId} jobId={slideJobId} docs={workspace.catalog.documents} />
            )}
            {view === "slides" && !slideJobId && (
                <SlidesView
                    docs={workspace.catalog.documents}
                    selected={workspace.selected}
                    setSelected={workspace.setSelected}
                    onUpload={() => workspace.setUploadOpen(true)}
                    onBrowseSources={
                        onBrowseSources ?? (() => workspace.setView("files"))
                    }
                    title={workspace.slideTitle}
                    setTitle={workspace.setSlideTitle}
                    count={workspace.slideCount}
                    setCount={workspace.setSlideCount}
                    guidance={workspace.guidance}
                    setGuidance={workspace.setGuidance}
                    tone={workspace.tone}
                    setTone={workspace.setTone}
                    job={null}
                    phase={workspace.slideSubmitting ? "submitting" : "idle"}
                    error={null}
                    warning={null}
                    start={onSlideStart ?? (async () => { await workspace.startSlides(); })}
                    retry={onSlideStart ?? (async () => { await workspace.startSlides(); })}
                />
            )}
            {view === "news" && (
                <NewsView docs={workspace.catalog.documents} onBrowseSources={onBrowseSources ?? (() => workspace.setView("files"))} />
            )}
            {view === "workflows" && <WorkflowList />}
            {workspace.uploadOpen && (
                <UploadModal
                    pending={workspace.pending}
                    setPending={workspace.setPending}
                    category={workspace.uploadCategory}
                    setCategory={workspace.setUploadCategory}
                    folderId={workspace.uploadFolderId}
                    setFolderId={workspace.setUploadFolderId}
                    folders={workspace.catalog.folders}
                    modes={workspace.modes}
                    uploading={workspace.uploading}
                    upload={workspace.upload}
                    close={() => workspace.setUploadOpen(false)}
                />
            )}
        </>
    );
}
