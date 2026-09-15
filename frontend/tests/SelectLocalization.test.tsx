import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FilesView } from "../components/workspace/FilesView";
import { UploadModal } from "../components/workspace/UploadModal";
import { FolderSelect } from "../components/workspace/DocumentSelects";

const modes = [
    { mode: "legislative_qa" as const, label: "立院問答", description: "" },
    { mode: "public_opinion" as const, label: "輿情", description: "" },
    { mode: "bei_can" as const, label: "備參", description: "" },
];

const folders = [
    {
        id: "folder-1",
        name: "政策資料",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
    },
];

describe("localized select values", () => {
    afterEach(() => cleanup());

    it("shows localized upload defaults instead of internal values", () => {
        render(
            <UploadModal
                pending={[]}
                setPending={vi.fn()}
                category="bei_can"
                setCategory={vi.fn()}
                folderId={null}
                setFolderId={vi.fn()}
                folders={folders}
                modes={modes}
                uploading={false}
                upload={vi.fn(async () => undefined)}
                close={vi.fn()}
            />,
        );

        const selectors = screen.getAllByRole("combobox");
        expect(selectors[0]).toHaveTextContent("備參");
        expect(selectors[0]).not.toHaveTextContent("bei_can");
        expect(selectors[1]).toHaveTextContent("未分類");
        expect(selectors[1]).not.toHaveTextContent("_uncategorized");
    });

    it("shows localized knowledge-base filter defaults", () => {
        render(
            <FilesView
                docs={[]}
                folders={folders}
                folderId={null}
                setFolderId={vi.fn()}
                folderName="全部文件"
                query=""
                setQuery={vi.fn()}
                onUpload={vi.fn()}
                onUpdate={vi.fn(async () => undefined)}
                onDelete={vi.fn()}
                onDownload={vi.fn()}
                onCreateFolder={vi.fn(async () => undefined)}
                onRenameFolder={vi.fn(async () => undefined)}
                onDeleteFolder={vi.fn(async () => undefined)}
                loading={false}
                error={null}
                actionError={null}
                getIngestion={vi.fn()}
                modes={modes}
            />,
        );

        expect(screen.getByRole("combobox", { name: "資料夾篩選" })).toHaveTextContent("全部資料夾");
        expect(screen.getByRole("combobox", { name: "分類篩選" })).toHaveTextContent("全部分類");
        expect(screen.getByRole("combobox", { name: "狀態篩選" })).toHaveTextContent("全部狀態");
    });

    it("uses a Chinese fallback when a selected folder no longer exists", () => {
        render(
            <FolderSelect
                folders={folders}
                value="deleted-folder"
                onValueChange={vi.fn()}
                aria-label="遺失資料夾"
            />,
        );

        const selector = screen.getByRole("combobox", { name: "遺失資料夾" });
        expect(selector).toHaveTextContent("未分類");
        expect(selector).not.toHaveTextContent("deleted-folder");
    });
});
