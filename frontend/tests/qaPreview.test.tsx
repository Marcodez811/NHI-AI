import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QaPreviewPanel } from "../components/chat/qa/QaPreviewPanel";
import type { QaDocument, QaVersion, QaVersionContent } from "../lib/api/qa";
import { clearQaVersionCache } from "../lib/hooks/useQaVersion";
import { changedParagraphs, chineseNumber, placeIssues } from "../lib/qa/preview";

const api = vi.hoisted(() => ({ fetchQaVersion: vi.fn() }));
vi.mock("../lib/api/qa", async (importOriginal) => ({
    ...(await importOriginal<typeof import("../lib/api/qa")>()),
    ...api,
}));

const doc = (overrides: Partial<QaDocument> = {}): QaDocument => ({
    kind: "ai",
    title: "11-5 立法院QA（2 題）",
    questions: [
        {
            no: 1, text: "點值為何？",
            short: [{ heading: "成效數據", paragraphs: [{ text: "點值 0.9594。", cites: [1] }, { text: "第二段短答。", cites: [] }] }],
            detail: [
                { heading: "背景說明", paragraphs: [{ text: "背景一。", cites: [1, 2] }] },
                { heading: "未來", paragraphs: [{ text: "未來一。", cites: [2] }] },
            ],
        },
        {
            no: 2, text: "獎勵方案？",
            short: [{ heading: null, paragraphs: [{ text: "無標題短答。", cites: [] }] }],
            detail: [{ heading: "背景說明", paragraphs: [{ text: "乙背景。", cites: [] }] }],
        },
    ],
    sources: ["甲.pdf", "乙.docx"],
    checked: true,
    issues: [],
    ...overrides,
});

const content = (id: string, document: QaDocument): QaVersionContent => ({
    artifact_id: id, kind: document.kind, created_at: "", download_url: `/api/v1/artifacts/${id}/download`, document,
});

const versions: QaVersion[] = [
    { artifact_id: "v2", title: "新版", created_at: "2026-11-24T05:00:00", download_url: "", kind: "ai", revised_from: "v1" },
    { artifact_id: "v1", title: "舊版", created_at: "2026-11-24T03:30:00", download_url: "", kind: "ai" },
];

function mockVersions(map: Record<string, QaDocument>) {
    api.fetchQaVersion.mockImplementation(async (_session: string, id: string) => content(id, map[id]));
}

function renderPanel(selectedId = "v1", props: Partial<React.ComponentProps<typeof QaPreviewPanel>> = {}) {
    const handlers = { onSelect: vi.fn(), onClose: vi.fn(), onRevise: vi.fn().mockResolvedValue(undefined) };
    render(<QaPreviewPanel sessionId="s1" versions={versions} selectedId={selectedId}
        generation={{ running: false, phase: null }} {...handlers} {...props} />);
    return handlers;
}

beforeEach(() => clearQaVersionCache());
afterEach(() => {
    cleanup();
    vi.clearAllMocks();
});

describe("preview helpers", () => {
    it("numbers like the document", () => {
        expect([1, 10, 11, 20, 21].map(chineseNumber)).toEqual(["一", "十", "十一", "二十", "二十一"]);
    });

    it("falls back to the nearest place that exists for an issue location", () => {
        const sections = doc().questions[0].detail;
        const issue = (section: number | null, paragraph: number | null) =>
            ({ question_no: 1, part: "detail" as const, kind: "style" as const, message: "m", section, paragraph });
        const placed = placeIssues([issue(null, null), issue(1, 0), issue(0, null), issue(0, 9), issue(7, 0)], sections);
        expect(placed.top).toHaveLength(2);
        expect(placed.byParagraph.get("1:0")).toHaveLength(1);
        expect(placed.bySection.get(0)).toHaveLength(2);
    });

    it("lists the paragraphs whose text differs from the base", () => {
        const next = doc();
        next.questions[0].short[0].paragraphs[0].text = "點值 0.9600。";
        next.questions[1].detail[0].paragraphs.push({ text: "新增", cites: [] });
        expect([...changedParagraphs(doc(), next)].sort()).toEqual(["1:short:0:0", "2:detail:0:1"]);
    });
});

describe("QaPreviewPanel", () => {
    it("renders the document structure: numbered headings, cited paragraphs and the source list", async () => {
        mockVersions({ v1: doc() });
        renderPanel();
        expect(await screen.findByText("一、成效數據：")).toBeInTheDocument();
        expect(screen.getByText("點值 0.9594。")).toBeInTheDocument();
        expect(screen.getByText("(一)")).toBeInTheDocument();
        expect(screen.getByText("(二)")).toBeInTheDocument();
        expect(screen.getByText("[1]", { selector: "sup" })).toBeInTheDocument();
        const sources = screen.getByRole("region", { name: "參考資料" });
        expect(within(sources).getByText("[2] 乙.docx")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "詳答" }));
        expect(screen.getByText("一、背景說明：")).toBeInTheDocument();
        expect(screen.getByText("二、未來：")).toBeInTheDocument();
        expect(screen.queryByText("一、成效數據：")).not.toBeInTheDocument();
    });

    it("switches questions by tab and numbers a heading-less short answer with 一、二、", async () => {
        mockVersions({ v1: doc() });
        renderPanel();
        await screen.findByText("一、成效數據：");
        await userEvent.click(screen.getByRole("tab", { name: "第 2 題" }));
        expect(screen.getByText("無標題短答。")).toBeInTheDocument();
        expect(screen.getByText("一、")).toBeInTheDocument();
        expect(screen.queryByText("點值 0.9594。")).not.toBeInTheDocument();
    });

    it("marks issues at the exact paragraph, at the part top without a place, and counts them per tab", async () => {
        const issues = [
            { question_no: 1, part: "short" as const, kind: "numeric" as const, message: "數字 0.9999 不在原文", section: 0, paragraph: 1 },
            { question_no: 1, part: "detail" as const, kind: "unsupported" as const, message: "詳答沒有依據" },
            { question_no: 2, part: "detail" as const, kind: "style" as const, message: "用了我們" },
        ];
        mockVersions({ v1: doc({ issues }) });
        renderPanel();
        await screen.findByText("一、成效數據：");
        expect(screen.getByRole("tab", { name: /第 1 題/ })).toHaveTextContent("⚠2");
        expect(screen.getByRole("tab", { name: /第 2 題/ })).toHaveTextContent("⚠1");
        const paragraphs = screen.getAllByText(/點值 0.9594。|第二段短答。/).map((node) => node.closest("[data-qa-paragraph]")!);
        expect(within(paragraphs[0] as HTMLElement).queryByRole("button", { name: /待確認/ })).not.toBeInTheDocument();
        const marker = within(paragraphs[1] as HTMLElement).getByRole("button", { name: "待確認 1 項" });
        expect(marker).toHaveAttribute("title", "數字 0.9999 不在原文");
        expect(screen.queryByText("數字與原文不符")).not.toBeInTheDocument();
        await userEvent.click(marker);
        expect(screen.getByText("數字與原文不符")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "詳答 ⚠1" }));
        expect(screen.getByText("這一部分有待確認的地方")).toBeInTheDocument();
        expect(screen.getByTitle("詳答沒有依據")).toBeInTheDocument();
    });

    it("lets the user switch versions, download and close", async () => {
        mockVersions({ v1: doc(), v2: doc() });
        const { onSelect, onClose } = renderPanel();
        await screen.findByText("一、成效數據：");
        expect(screen.getByRole("link", { name: /下載 Word/ })).toHaveAttribute("href", "/api/v1/artifacts/v1/download");
        await userEvent.click(screen.getByRole("combobox", { name: "切換版本" }));
        await userEvent.click(await screen.findByRole("option", { name: /修訂.*11\/24 05:00/ }));
        expect(onSelect).toHaveBeenCalledWith("v2");
        await userEvent.click(screen.getByRole("button", { name: "關閉預覽" }));
        expect(onClose).toHaveBeenCalled();
    });

    it("highlights what a revision changed against its base", async () => {
        const revised = doc({ revised_from: "v1", revision_note: "語氣肯定" });
        revised.questions[0].short[0].paragraphs[0].text = "本署點值為 0.9594。";
        mockVersions({ v1: doc(), v2: revised });
        renderPanel("v2");
        const changed = await screen.findByText("本署點值為 0.9594。");
        await waitFor(() => expect(changed.closest("[data-qa-paragraph]")).toHaveAttribute("data-changed", "true"));
        expect(screen.getByText("第二段短答。").closest("[data-qa-paragraph]")).not.toHaveAttribute("data-changed");
        expect(screen.getByText(/標示處為相較 11\/24 03:30 版本修改的段落（語氣肯定）/)).toBeInTheDocument();
    });

    it("revises the current question and part with the typed instruction", async () => {
        mockVersions({ v1: doc() });
        const { onRevise } = renderPanel();
        await screen.findByText("一、成效數據：");
        await userEvent.click(screen.getByRole("tab", { name: "第 2 題" }));
        await userEvent.click(screen.getByRole("button", { name: "詳答" }));
        await userEvent.click(screen.getByRole("button", { name: "修改這段" }));
        const submit = screen.getByRole("button", { name: "送出修改" });
        expect(submit).toBeDisabled();
        await userEvent.type(screen.getByRole("textbox", { name: "第 2 題詳答修改意見" }), "更肯定，但不能說有預算");
        await userEvent.click(submit);
        expect(onRevise).toHaveBeenCalledWith({
            question_no: 2, part: "detail", instruction: "更肯定，但不能說有預算", base_artifact_id: "v1",
        });
        await waitFor(() => expect(screen.getByRole("button", { name: "修改這段" })).toBeInTheDocument());
    });

    it("shows a failed revision and disables revising while one runs; outline versions cannot be revised", async () => {
        mockVersions({ v1: doc() });
        const { onRevise } = renderPanel();
        onRevise.mockRejectedValueOnce(new Error("x"));
        await screen.findByText("一、成效數據：");
        await userEvent.click(screen.getByRole("button", { name: "修改這段" }));
        await userEvent.type(screen.getByRole("textbox"), "改");
        await userEvent.click(screen.getByRole("button", { name: "送出修改" }));
        expect(await screen.findByRole("alert")).toHaveTextContent("操作失敗");
        cleanup();
        mockVersions({ v1: doc() });
        renderPanel("v1", { generation: { running: true, phase: "drafting" } });
        await screen.findByText("一、成效數據：");
        expect(screen.getByRole("button", { name: "修改這段" })).toBeDisabled();
        expect(screen.getByRole("status")).toHaveTextContent("AI 撰寫中");
        cleanup();
        clearQaVersionCache();
        mockVersions({ v1: doc({ kind: "outline" }) });
        renderPanel();
        await screen.findByText("一、成效數據：");
        expect(screen.queryByRole("button", { name: "修改這段" })).not.toBeInTheDocument();
    });

    it("explains a version that cannot be loaded", async () => {
        api.fetchQaVersion.mockRejectedValue(new Error("boom"));
        renderPanel();
        expect(await screen.findByRole("alert")).toHaveTextContent("無法載入這個版本");
    });
});
