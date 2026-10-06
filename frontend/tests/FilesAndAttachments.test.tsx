import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
    fetchUserFiles: vi.fn(),
    promoteUserFile: vi.fn(),
    deleteUserFile: vi.fn(),
    fetchArtifacts: vi.fn(),
    deleteArtifact: vi.fn(),
    fetchDocuments: vi.fn(),
    fetchFolders: vi.fn(),
}));
vi.mock("../lib/api/files", () => ({
    fetchUserFiles: api.fetchUserFiles,
    promoteUserFile: api.promoteUserFile,
    deleteUserFile: api.deleteUserFile,
    userFileContentUrl: (id: string) => `/files/${id}/content`,
}));
vi.mock("../lib/api/artifacts", () => ({
    fetchArtifacts: api.fetchArtifacts,
    deleteArtifact: api.deleteArtifact,
    artifactDownloadUrl: (id: string) => `/artifacts/${id}/download`,
    ARTIFACT_KIND_LABELS: { slide_deck: "簡報", news_draft: "新聞稿", report: "報告" },
}));
vi.mock("../lib/api/documents", async (orig) => ({
    ...(await orig<typeof import("../lib/api/documents")>()),
    fetchDocuments: api.fetchDocuments,
    fetchFolders: api.fetchFolders,
}));
const nav = vi.hoisted(() => ({ tab: null as string | null, push: vi.fn() }));
vi.mock("next/navigation", () => ({
    usePathname: () => "/files",
    useRouter: () => ({ push: nav.push }),
    useSearchParams: () => new URLSearchParams(nav.tab ? `tab=${nav.tab}` : ""),
}));

import { ChatComposer } from "../components/chat/ChatComposer";
import { ChatAttachmentChip } from "../components/chat/ChatAttachmentChip";
import { FilesPage } from "../components/files/FilesPage";
import type { ChatAttachment } from "../lib/api/chat";

const uploaded = (over: Partial<ChatAttachment> = {}): ChatAttachment => ({
    id: "f1", display_name: "報告.pdf", mime_type: "application/pdf", kind: "document",
    size_bytes: 10, status: "ready", error: null, text_chars: 5, created_at: "2026-01-01T00:00:00Z", ...over,
});

function renderComposer(over: Partial<React.ComponentProps<typeof ChatComposer>> = {}) {
    const props: React.ComponentProps<typeof ChatComposer> = {
        sessionId: "s1", draft: "", setDraft: vi.fn(), attachments: [],
        onAttachFiles: vi.fn().mockResolvedValue([uploaded()]),
        onAttachExisting: vi.fn().mockResolvedValue(undefined),
        onRemoveAttachment: vi.fn(),
        models: [], model: undefined, setModel: vi.fn(), modelsLoading: false,
        busy: false, uploadsPending: false, onSend: vi.fn(), onStop: vi.fn(), ...over,
    };
    const view = render(<ChatComposer {...props} />);
    return { props, view };
}

describe("composer plus menu and upload modal", () => {
    beforeEach(() => {
        api.fetchFolders.mockResolvedValue([]);
        api.fetchDocuments.mockResolvedValue([]);
        api.fetchUserFiles.mockResolvedValue([]);
        api.promoteUserFile.mockResolvedValue(undefined);
    });
    afterEach(() => { cleanup(); vi.clearAllMocks(); });

    it("shows four menu items with 使用技能 disabled", async () => {
        renderComposer();
        await userEvent.click(screen.getByRole("button", { name: "附加檔案" }));
        const items = await screen.findAllByRole("menuitem");
        expect(items.map((item) => item.textContent?.trim())).toEqual([
            "上傳檔案", "從知識庫加入", "從我的檔案加入", "使用技能即將推出",
        ]);
        expect(items[3]).toHaveAttribute("aria-disabled", "true");
    });

    it("defaults the upload modal to 只用於此對話 and does not promote", async () => {
        const { view } = renderComposer();
        const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
        await userEvent.upload(input, new File(["x"], "報告.pdf", { type: "application/pdf" }));
        const dialog = await screen.findByRole("dialog");
        expect(within(dialog).getByLabelText("只用於此對話")).toBeChecked();
        await userEvent.click(within(dialog).getByRole("button", { name: "確定" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        expect(api.promoteUserFile).not.toHaveBeenCalled();
    });

    it("promotes with the chosen category when 同時加入知識庫 is picked", async () => {
        const { view } = renderComposer();
        const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
        await userEvent.upload(input, new File(["x"], "報告.pdf", { type: "application/pdf" }));
        const dialog = await screen.findByRole("dialog");
        await userEvent.click(within(dialog).getByLabelText("同時加入知識庫"));
        expect(within(dialog).getByText("加入後所有人都能搜尋到，並需數分鐘建立索引。")).toBeInTheDocument();
        expect(within(dialog).getByRole("button", { name: "確定" })).toBeDisabled();
        await userEvent.click(within(dialog).getByRole("combobox", { name: "分類" }));
        await userEvent.click(await screen.findByRole("option", { name: "輿情" }));
        await userEvent.click(within(dialog).getByRole("button", { name: "確定" }));
        await waitFor(() => expect(api.promoteUserFile).toHaveBeenCalledWith("f1", { category: "public_opinion", folder_id: null }));
    });

    it("skips the modal for pasted images", () => {
        const { props } = renderComposer();
        const image = new File(["x"], "a.png", { type: "image/png" });
        fireEvent.paste(screen.getByPlaceholderText("問問健保署 AI…"), { clipboardData: { files: [image] } });
        expect(props.onAttachFiles).toHaveBeenCalledWith([image]);
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("attaches selected knowledge-base documents", async () => {
        api.fetchDocuments.mockResolvedValue([{
            id: "d1", display_name: "健保法規", original_filename: "x.pdf", mime_type: "application/pdf",
            size_bytes: 5, category: "legislative_qa",
        }]);
        const { props } = renderComposer();
        await userEvent.click(screen.getByRole("button", { name: "附加檔案" }));
        await userEvent.click(await screen.findByRole("menuitem", { name: /從知識庫加入/ }));
        await userEvent.click(await screen.findByLabelText(/健保法規/));
        await userEvent.click(screen.getByRole("button", { name: "加入對話" }));
        await waitFor(() => expect(props.onAttachExisting).toHaveBeenCalledWith(
            "knowledge_base", [expect.objectContaining({ id: "d1" })],
        ));
    });

    it("attaches selected files from my files", async () => {
        api.fetchUserFiles.mockResolvedValue([{
            id: "u1", display_name: "舊檔.docx", mime_type: "x", kind: "document", size_bytes: 1,
            status: "ready", created_at: "", origin_session_id: null, origin_session_title: "舊對話", in_knowledge_base: false,
        }]);
        const { props } = renderComposer();
        await userEvent.click(screen.getByRole("button", { name: "附加檔案" }));
        await userEvent.click(await screen.findByRole("menuitem", { name: /從我的檔案加入/ }));
        await userEvent.click(await screen.findByLabelText(/舊檔\.docx/));
        await userEvent.click(screen.getByRole("button", { name: "加入對話" }));
        await waitFor(() => expect(props.onAttachExisting).toHaveBeenCalledWith(
            "upload", [expect.objectContaining({ id: "u1" })],
        ));
    });
});

describe("attachment chip", () => {
    afterEach(cleanup);
    it("badges knowledge-base documents and removing calls onRemove", async () => {
        const onRemove = vi.fn();
        render(<ChatAttachmentChip sessionId="s1" onRemove={onRemove}
            item={{ localId: "l1", status: "ready", attachment: uploaded({ source: "knowledge_base" }) }} />);
        expect(screen.getByText("知識庫")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "移除 報告.pdf" }));
        expect(onRemove).toHaveBeenCalled();
    });
});

describe("/files page", () => {
    beforeEach(() => {
        nav.tab = null;
        api.fetchFolders.mockResolvedValue([]);
        api.fetchUserFiles.mockResolvedValue([{
            id: "u1", display_name: "舊檔.pdf", mime_type: "application/pdf", kind: "document", size_bytes: 2048,
            status: "ready", created_at: "2026-01-01T00:00:00Z", origin_session_id: "s9", origin_session_title: "藥價討論", in_knowledge_base: true,
        }]);
        api.fetchArtifacts.mockResolvedValue([]);
        api.deleteUserFile.mockResolvedValue(undefined);
    });
    afterEach(() => { cleanup(); vi.clearAllMocks(); });

    it("renders both tabs and the file row", async () => {
        render(<FilesPage />);
        expect(screen.getByRole("tab", { name: "上傳的檔案" })).toBeInTheDocument();
        expect(screen.getByRole("tab", { name: "產出的文件" })).toBeInTheDocument();
        expect(await screen.findByText("舊檔.pdf")).toBeInTheDocument();
        expect(screen.getByText("已加入知識庫")).toBeInTheDocument();
        expect(screen.getByRole("link", { name: /藥價討論/ })).toHaveAttribute("href", "/chat/s9");
    });

    it("shows a Chinese empty state on the artifacts tab", async () => {
        nav.tab = "artifacts";
        render(<FilesPage />);
        expect(await screen.findByText(/還沒有產出的文件/)).toBeInTheDocument();
    });

    it("confirms before deleting", async () => {
        render(<FilesPage />);
        await userEvent.click(await screen.findByRole("button", { name: /刪除/ }));
        expect(api.deleteUserFile).not.toHaveBeenCalled();
        const dialog = await screen.findByRole("alertdialog");
        await userEvent.click(within(dialog).getByRole("button", { name: "刪除" }));
        await waitFor(() => expect(api.deleteUserFile).toHaveBeenCalledWith("u1"));
    });
});
