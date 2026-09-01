import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChatView } from "../components/workspace/ChatView";
import { fetchRetrievalStatus, type RetrievalStatus } from "../lib/api";
import { useRetrievalStatus } from "../lib/hooks/useRetrievalStatus";

vi.mock("../lib/api/retrieval", () => ({
    fetchRetrievalStatus: vi.fn(),
}));

import * as retrievalApi from "../lib/api/retrieval";

const modes = [
    {
        mode: "legislative_qa" as const,
        label: "立法院問答",
        description: "立法院相關文件",
    },
];

const status = (overrides: Partial<RetrievalStatus> = {}): RetrievalStatus => ({
    state: "ready",
    can_retrieve: true,
    ready_document_count: 1,
    error_code: null,
    warning_code: null,
    ...overrides,
});

function renderChat(overrides: Partial<React.ComponentProps<typeof ChatView>> = {}) {
    return render(
        <ChatView
            chat={[]}
            draft=""
            setDraft={vi.fn()}
            send={vi.fn()}
            scope="legislative_qa"
            setScope={vi.fn()}
            modes={modes}
            modesError={null}
            retryModes={vi.fn()}
            busy={false}
            error={null}
            eligibilityError={null}
            retrievalStatus={status()}
            retrievalLoading={false}
            retrievalError={null}
            retryRetrieval={vi.fn()}
            catalogLoading={false}
            hasAnyReadyDocuments
            hasCategoryReadyDocuments
            onUploadSources={vi.fn()}
            {...overrides}
        />,
    );
}

describe("retrieval status transport", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it("reads the sanitized bootstrap status endpoint", async () => {
        const payload = status({ state: "provisioning", can_retrieve: false, ready_document_count: 0 });
        const fetchMock = vi.fn(async () =>
            new Response(JSON.stringify(payload), {
                status: 200,
                headers: { "Content-Type": "application/json" },
            }),
        );
        vi.stubGlobal("fetch", fetchMock);

        await expect(fetchRetrievalStatus()).resolves.toEqual(payload);
        expect(fetchMock).toHaveBeenCalledWith(
            "/api/v1/retrieval/status",
            expect.objectContaining({ headers: expect.any(Headers) }),
        );
    });
});

describe("useRetrievalStatus", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.mocked(retrievalApi.fetchRetrievalStatus).mockReset();
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("polls while the backend is provisioning and stops once ready", async () => {
        vi.mocked(retrievalApi.fetchRetrievalStatus)
            .mockResolvedValueOnce(status({ state: "provisioning", can_retrieve: false, ready_document_count: 0 }))
            .mockResolvedValueOnce(status());

        const { result } = renderHook(() =>
            useRetrievalStatus({ pollIntervalMs: 100 }),
        );

        await act(async () => {
            await Promise.resolve();
        });
        expect(result.current.status?.state).toBe("provisioning");

        await act(async () => {
            await vi.advanceTimersByTimeAsync(100);
        });
        expect(result.current.status?.state).toBe("ready");

        await act(async () => {
            await vi.advanceTimersByTimeAsync(500);
        });
        expect(retrievalApi.fetchRetrievalStatus).toHaveBeenCalledTimes(2);
    });
});

describe("ChatView retrieval empty states", () => {
    afterEach(() => cleanup());

    it("guides users to upload the first document and hides the composer", async () => {
        const user = userEvent.setup();
        const onUploadSources = vi.fn();
        renderChat({
            onUploadSources,
            hasAnyReadyDocuments: false,
            hasCategoryReadyDocuments: false,
            retrievalStatus: status({ ready_document_count: 0 }),
        });

        expect(screen.getByText("先上傳第一份文件")).toBeInTheDocument();
        expect(screen.getByText("將健保政策資料加入知識庫，完成索引後即可提出有依據的問題。")).toBeInTheDocument();
        expect(screen.queryByRole("textbox")).not.toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: /上傳第一份文件/ }));
        expect(onUploadSources).toHaveBeenCalledOnce();
    });

    it("shows a category-specific empty state while keeping scope selection available", () => {
        renderChat({ hasCategoryReadyDocuments: false });

        expect(screen.getByText("此搜尋範圍尚無可用文件")).toBeInTheDocument();
        expect(screen.getByRole("combobox", { name: "搜尋範圍" })).toBeInTheDocument();
    });

    it("offers retry when vector-store readiness fails", async () => {
        const user = userEvent.setup();
        const retryRetrieval = vi.fn();
        renderChat({
            retrievalStatus: status({ state: "error", can_retrieve: false, ready_document_count: 0 }),
            retryRetrieval,
        });

        expect(screen.getByText("知識庫尚未就緒")).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: /重新檢查/ }));
        expect(retryRetrieval).toHaveBeenCalledOnce();
    });
});

