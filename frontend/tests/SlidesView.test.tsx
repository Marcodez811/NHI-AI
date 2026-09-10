import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DocumentRead } from "../lib/api";
import { SlidesView } from "../components/workspace/WorkspaceViews";

const document = (overrides: Partial<DocumentRead> = {}): DocumentRead => ({
    id: "doc-1",
    original_filename: "brief.pdf",
    display_name: "brief.pdf",
    mime_type: "application/pdf",
    extension: ".pdf",
    size_bytes: 1,
    checksum: "checksum",
    category: "legislative_qa",
    folder_id: null,
    retrieval_enabled: true,
    status: "ready",
    stage: "ready",
    error: null,
    source_priority: null,
    roles: [],
    page_count: null,
    table_count: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
});

function renderView({ docs = [], selected = [] }: { docs?: DocumentRead[]; selected?: string[] } = {}) {
    function Wrapper() {
        const [currentSelection, setCurrentSelection] = useState(selected);
        return (
            <SlidesView
                docs={docs}
                selected={currentSelection}
                setSelected={setCurrentSelection}
                onUpload={vi.fn()}
                onBrowseSources={vi.fn()}
                title="政策重點整理"
                setTitle={vi.fn()}
                count={10}
                setCount={vi.fn()}
                guidance=""
                setGuidance={vi.fn()}
                tone="formal"
                setTone={vi.fn()}
                job={null}
                phase="idle"
                error={null}
                warning={null}
                start={vi.fn(async () => undefined)}
                retry={vi.fn(async () => undefined)}
            />
        );
    }
    return render(<Wrapper />);
}

describe("SlidesView", () => {
    afterEach(() => cleanup());

    it("guides users through the empty source state", async () => {
        const user = userEvent.setup();
        const onUpload = vi.fn();
        const onBrowseSources = vi.fn();
        render(
            <SlidesView
                docs={[]}
                selected={[]}
                setSelected={vi.fn()}
                onUpload={onUpload}
                onBrowseSources={onBrowseSources}
                title="政策重點整理"
                setTitle={vi.fn()}
                count={10}
                setCount={vi.fn()}
                guidance=""
                setGuidance={vi.fn()}
                tone="formal"
                setTone={vi.fn()}
                job={null}
                phase="idle"
                error={null}
                warning={null}
                start={vi.fn(async () => undefined)}
                retry={vi.fn(async () => undefined)}
            />,
        );

        expect(screen.getByText("先加入來源文件")).toBeInTheDocument();
        expect(screen.getByText("知識庫目前沒有文件。")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "生成簡報" })).toBeDisabled();
        expect(screen.getByText("選取至少一份可用來源後即可生成。")).toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: "上傳來源文件" }));
        await user.click(screen.getByRole("button", { name: "前往知識庫" }));
        expect(onUpload).toHaveBeenCalledOnce();
        expect(onBrowseSources).toHaveBeenCalledOnce();
    });

    it("shows a ready state for selected supported sources", () => {
        renderView({ docs: [document()], selected: ["doc-1"] });

        expect(screen.getByText("來源已就緒，可以生成。")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "生成簡報" })).toBeEnabled();
        expect(screen.getByRole("button", { name: "正式" })).toHaveAttribute("aria-pressed", "true");
        expect(screen.getByLabelText("簡報標題")).toHaveValue("政策重點整理");
    });

    it("allows a new generation after a failed job", async () => {
        const user = userEvent.setup();
        const start = vi.fn(async () => undefined);
        render(
            <SlidesView
                docs={[document()]}
                selected={["doc-1"]}
                setSelected={vi.fn()}
                onUpload={vi.fn()}
                onBrowseSources={vi.fn()}
                title="政策重點整理"
                setTitle={vi.fn()}
                count={10}
                setCount={vi.fn()}
                guidance=""
                setGuidance={vi.fn()}
                tone="formal"
                setTone={vi.fn()}
                job={{
                    job_id: "job-failed",
                    status: "failed",
                    phase: "failed",
                    stage: "failed",
                    message: "Presentation generation failed.",
                    started_at: null,
                    finished_at: "2026-09-02T00:00:00Z",
                    error: "generation failed",
                    download_url: null,
                }}
                phase="failed"
                error="generation failed"
                warning={null}
                start={start}
                retry={start}
            />,
        );

        expect(screen.getByText("來源已就緒，可以生成。")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "生成簡報" })).toBeEnabled();
        await user.click(screen.getByRole("button", { name: "生成簡報" }));
        expect(start).toHaveBeenCalledOnce();
    });

    it("returns to the editable form instead of resubmitting a completed job", async () => {
        const user = userEvent.setup();
        const onNewPresentation = vi.fn();
        const start = vi.fn(async () => undefined);
        render(
            <SlidesView
                docs={[document()]}
                selected={["doc-1"]}
                setSelected={vi.fn()}
                onUpload={vi.fn()}
                onBrowseSources={vi.fn()}
                title="changed title"
                setTitle={vi.fn()}
                count={10}
                setCount={vi.fn()}
                guidance=""
                setGuidance={vi.fn()}
                tone="formal"
                setTone={vi.fn()}
                job={{
                    job_id: "job-completed",
                    status: "completed",
                    phase: "completed",
                    stage: "completed",
                    message: "done",
                    started_at: null,
                    finished_at: null,
                    error: null,
                    download_url: "/download",
                    brief: {
                        title: "original title",
                        document_ids: ["doc-1"],
                        slides_count: 10,
                        guidance: "",
                        tone: "formal",
                    },
                }}
                phase="completed"
                error={null}
                warning={null}
                start={start}
                retry={start}
                onNewPresentation={onNewPresentation}
            />,
        );

        expect(screen.getByText("original title")).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "建立新簡報" }));
        expect(onNewPresentation).toHaveBeenCalledOnce();
        expect(start).not.toHaveBeenCalled();
    });

    it("offers an inline picker for available sources", async () => {
        const user = userEvent.setup();
        renderView({ docs: [document()] });

        await user.click(screen.getByRole("button", { name: "選取來源文件" }));
        const checkbox = screen.getByRole("checkbox", { name: "brief.pdf" });
        expect(checkbox).not.toBeChecked();
        await user.click(checkbox);
        expect(checkbox).toBeChecked();
        expect(screen.getByRole("button", { name: "完成" })).toBeInTheDocument();
    });

    it("keeps generation disabled while a selected source is indexing", () => {
        renderView({
            docs: [document({ status: "indexing", stage: "indexing" })],
            selected: ["doc-1"],
        });

        expect(screen.getByText("來源索引狀態")).toBeInTheDocument();
        expect(screen.getByText("1 份選取文件正在索引，完成後才能生成。"))
            .toBeInTheDocument();
        expect(screen.getByRole("button", { name: "生成簡報" })).toBeDisabled();
    });
});
