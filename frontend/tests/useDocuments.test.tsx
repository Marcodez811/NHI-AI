import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
    ApiError,
    type DocumentRead,
    type FolderRead,
} from "../lib/api";
import * as api from "../lib/api";
import { useDocuments } from "../lib/hooks/useDocuments";

vi.mock("../lib/api", async () => {
    const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
    return {
        ...actual,
        fetchDocuments: vi.fn(),
        fetchFolders: vi.fn(),
        getDocument: vi.fn(),
        getIngestionStatus: vi.fn(),
        uploadDocument: vi.fn(),
        updateDocument: vi.fn(),
        deleteDocument: vi.fn(),
        createFolder: vi.fn(),
        renameFolder: vi.fn(),
        deleteFolder: vi.fn(),
    };
});

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

const folder = (overrides: Partial<FolderRead> = {}): FolderRead => ({
    id: "folder-1",
    name: "政策",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
});

describe("useDocuments", () => {
    beforeEach(() => {
        vi.resetAllMocks();
        vi.mocked(api.fetchDocuments).mockResolvedValue([]);
        vi.mocked(api.fetchFolders).mockResolvedValue([]);
    });

    it("loads documents and folders independently", async () => {
        vi.mocked(api.fetchDocuments).mockResolvedValue([document()]);
        vi.mocked(api.fetchFolders).mockRejectedValue(
            new ApiError(503, "Folders unavailable"),
        );

        const { result } = renderHook(() => useDocuments());
        await waitFor(() => expect(result.current.loading).toBe(false));

        expect(result.current.documents).toEqual([document()]);
        expect(result.current.folders).toEqual([]);
        expect(result.current.documentsError).toBeNull();
        expect(result.current.foldersError).toMatchObject({
            status: 503,
            message: "Folders unavailable",
        });
    });

    it("replaces server state after upload, update, and asynchronous delete", async () => {
        const initial = document();
        const queued = document({ status: "queued", stage: "queued" });
        const renamed = document({ display_name: "renamed.pdf" });
        const deleting = document({ status: "deleting", stage: "deletion_queued", retrieval_enabled: false });
        vi.mocked(api.fetchDocuments).mockResolvedValue([initial]);
        vi.mocked(api.uploadDocument).mockResolvedValue({
            ...queued,
            id: "doc-2",
            ingestion_job_id: "ingest-2",
        });
        vi.mocked(api.updateDocument).mockResolvedValue(renamed);
        vi.mocked(api.deleteDocument).mockResolvedValue(deleting);

        const { result } = renderHook(() => useDocuments());
        await waitFor(() => expect(result.current.documents).toHaveLength(1));

        await act(async () => {
            await result.current.upload(new File(["source"], "source.pdf"), "legislative_qa");
        });
        expect(result.current.documents.map((item) => item.id)).toEqual(["doc-1", "doc-2"]);

        await act(async () => {
            await result.current.updateDocument("doc-1", { display_name: "renamed.pdf" });
        });
        expect(result.current.documents.find((item) => item.id === "doc-1")?.display_name).toBe("renamed.pdf");

        await act(async () => {
            await result.current.deleteDocument("doc-1");
        });
        expect(result.current.documents.find((item) => item.id === "doc-1")?.status).toBe("deleting");
    });

    it("updates folder state only after successful folder mutations", async () => {
        const initial = folder();
        vi.mocked(api.fetchFolders).mockResolvedValue([initial]);
        vi.mocked(api.createFolder).mockResolvedValue(folder({ id: "folder-2", name: "新資料夾" }));
        vi.mocked(api.renameFolder).mockResolvedValue(folder({ name: "已重新命名" }));
        vi.mocked(api.deleteFolder).mockResolvedValue(undefined);

        const { result } = renderHook(() => useDocuments());
        await waitFor(() => expect(result.current.folders).toHaveLength(1));

        await act(async () => {
            await result.current.createFolder("新資料夾");
        });
        await act(async () => {
            await result.current.renameFolder("folder-1", "已重新命名");
        });
        expect(result.current.folders.map((item) => item.name)).toEqual(["已重新命名", "新資料夾"]);
        await act(async () => {
            await result.current.deleteFolder("folder-1");
        });
        expect(result.current.folders.map((item) => item.id)).toEqual(["folder-2"]);
    });
});
