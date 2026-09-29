import { describe, expect, it } from "vitest";

import { analyzeSlideReadiness } from "../components/workspace/slide-readiness";
import type { DocumentRead } from "../lib/api/documents";

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

describe("slide readiness", () => {
    it.each([
        ["indexing", "正在索引"],
        ["failed", "索引失敗"],
        ["deleting", "正在刪除"],
    ] as const)("blocks a %s source with actionable copy", (status, copy) => {
        const result = analyzeSlideReadiness({
            docs: [document({ status })],
            selected: ["doc-1"],
            title: "政策簡報",
            phase: "idle",
        });

        expect(result.canGenerate).toBe(false);
        expect(result.readinessMessage).toContain(copy);
    });

    it("blocks unsupported ready sources and enables supported ready sources", () => {
        const unsupported = analyzeSlideReadiness({
            docs: [document({ extension: ".xlsx" })],
            selected: ["doc-1"],
            title: "政策簡報",
            phase: "idle",
        });
        expect(unsupported.canGenerate).toBe(false);
        expect(unsupported.readinessMessage).toContain("不支援");

        const ready = analyzeSlideReadiness({
            docs: [document()],
            selected: ["doc-1"],
            title: "政策簡報",
            phase: "idle",
        });
        expect(ready.canGenerate).toBe(true);
        expect(ready.readinessMessage).toBe("來源已就緒，可以生成。");
    });
});
