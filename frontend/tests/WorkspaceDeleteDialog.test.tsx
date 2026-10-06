import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceContent } from "../components/workspace/WorkspaceContent";

vi.mock("../components/workspace/FilesView", () => ({ FilesView: () => null }));
vi.mock("../components/workspace/SlidesView", () => ({ SlidesView: () => null }));
vi.mock("../components/workspace/SlideJobView", () => ({ SlideJobView: () => null }));
vi.mock("../components/workspace/NewsView", () => ({ NewsView: () => null }));
vi.mock("../components/workspace/UploadModal", () => ({ UploadModal: () => null }));
vi.mock("../components/workspace/WorkflowList", () => ({ WorkflowList: () => null }));

afterEach(() => cleanup());

function setup(pending: boolean) {
    const confirmDeleteDocument = vi.fn();
    const cancelDeleteDocument = vi.fn();
    const workspace = {
        catalog: { error: null },
        actionError: null,
        pendingDelete: pending ? { id: "d1", display_name: "指引.pdf" } : null,
        confirmDeleteDocument,
        cancelDeleteDocument,
    };
    render(<WorkspaceContent workspace={workspace as never} view={"news" as never} />);
    return { confirmDeleteDocument, cancelDeleteDocument };
}

it("asks before deleting a document and only deletes after confirming", async () => {
    const user = userEvent.setup();
    const { confirmDeleteDocument } = setup(true);
    expect(screen.getByText("刪除「指引.pdf」？")).toBeInTheDocument();
    expect(confirmDeleteDocument).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "刪除" }));
    expect(confirmDeleteDocument).toHaveBeenCalledTimes(1);
});

it("cancelling the dialog does not delete", async () => {
    const user = userEvent.setup();
    const { confirmDeleteDocument, cancelDeleteDocument } = setup(true);
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(cancelDeleteDocument).toHaveBeenCalled();
    expect(confirmDeleteDocument).not.toHaveBeenCalled();
});

it("renders no dialog without a pending delete", () => {
    setup(false);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
});
