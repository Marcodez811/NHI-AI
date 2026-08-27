"use client";

import { ChatView, FilesView, SlidesView, Sidebar, UploadModal } from "./WorkspaceViews";
import type { WorkspaceController } from "../../lib/hooks/useWorkspaceController";

export function WorkspaceShell({ workspace }: { workspace: WorkspaceController }) {
    const { catalog, slideJob } = workspace;
    const catalogError = catalog.error?.message || null;

    return (
        <div className="min-h-screen bg-background text-foreground">
            <Sidebar
                view={workspace.view}
                setView={workspace.setView}
                collapsed={workspace.collapsed}
                setCollapsed={workspace.setCollapsed}
                documentCount={catalog.documents.length}
                hasError={Boolean(catalog.error || workspace.actionError)}
            />
            <main className={`${workspace.collapsed ? "md:pl-20" : "md:pl-64"} min-h-screen pb-24 transition-all md:pb-0`}>
                <header className="flex h-14 items-center border-b border-border bg-background/90 px-5 md:hidden">
                    <MobileWorkspaceLabel view={workspace.view} />
                </header>
                {workspace.actionError && <div className="mx-5 mt-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{workspace.actionError}</div>}

                {workspace.view === "chat" && (
                    <ChatView
                        chat={workspace.chat}
                        draft={workspace.draft}
                        setDraft={workspace.setDraft}
                        send={workspace.send}
                        scope={workspace.scope}
                        setScope={workspace.changeScope}
                        modes={workspace.modes}
                        modesError={workspace.qaError}
                        retryModes={() => void workspace.loadModes()}
                        busy={workspace.chatBusy}
                        error={workspace.chatError}
                        eligibilityError={workspace.eligibilityError}
                    />
                )}
                {workspace.view === "files" && (
                    <FilesView
                        docs={catalog.documents}
                        folders={catalog.folders}
                        folderId={workspace.folderId}
                        setFolderId={workspace.setFolderId}
                        folderName={workspace.folderName}
                        query={workspace.query}
                        setQuery={workspace.setQuery}
                        selected={workspace.selected}
                        onToggle={workspace.toggleSelected}
                        onUpload={() => workspace.setUploadOpen(true)}
                        onUpdate={workspace.mutateDocument}
                        onDelete={workspace.deleteDocument}
                        onDownload={workspace.downloadDocument}
                        onCreateFolder={workspace.createFolder}
                        onRenameFolder={workspace.renameFolder}
                        onDeleteFolder={workspace.deleteFolder}
                        loading={catalog.loading}
                        error={catalogError}
                        actionError={workspace.actionError}
                        getIngestion={catalog.getIngestion}
                        modes={workspace.modes}
                    />
                )}
                {workspace.view === "slides" && (
                    <SlidesView
                        docs={catalog.documents}
                        selected={workspace.selected}
                        setSelected={workspace.setSelected}
                        onUpload={() => workspace.setUploadOpen(true)}
                        onBrowseSources={() => workspace.setView("files")}
                        title={workspace.slideTitle}
                        setTitle={workspace.setSlideTitle}
                        count={workspace.slideCount}
                        setCount={workspace.setSlideCount}
                        guidance={workspace.guidance}
                        setGuidance={workspace.setGuidance}
                        tone={workspace.tone}
                        setTone={workspace.setTone}
                        job={slideJob.job}
                        phase={slideJob.phase}
                        phaseHistory={slideJob.phaseHistory}
                        error={slideJob.error?.message || null}
                        warning={slideJob.warning}
                        pollNow={slideJob.pollNow}
                        start={workspace.startSlides}
                        retry={async () => { await slideJob.retry(); }}
                    />
                )}
            </main>
            {workspace.uploadOpen && (
                <UploadModal
                    pending={workspace.pending}
                    setPending={workspace.setPending}
                    category={workspace.uploadCategory}
                    setCategory={workspace.setUploadCategory}
                    folderId={workspace.uploadFolderId}
                    setFolderId={workspace.setUploadFolderId}
                    folders={catalog.folders}
                    modes={workspace.modes}
                    uploading={workspace.uploading}
                    upload={workspace.upload}
                    close={() => workspace.setUploadOpen(false)}
                />
            )}
        </div>
    );
}

function MobileWorkspaceLabel({ view }: { view: WorkspaceController["view"] }) {
    const labels: Record<WorkspaceController["view"], string> = {
        chat: "對話",
        files: "知識庫",
        slides: "簡報生成",
    };
    return (
        <div className="flex items-center gap-2 text-sm font-semibold">
            <span className="size-2 rounded-full bg-primary" aria-hidden="true" />
            {labels[view]}
        </div>
    );
}
