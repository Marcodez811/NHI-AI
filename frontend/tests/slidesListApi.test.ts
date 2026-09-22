import { afterEach, describe, expect, it, vi } from "vitest";

import { listSlideJobs } from "../lib/api/slides";

describe("listSlideJobs", () => {
    afterEach(() => vi.unstubAllGlobals());

    it("requests a bounded recent-job list through the slides API", async () => {
        const items = [{
            job_id: "job-1",
            title: "年度政策簡報",
            status: "queued",
            phase: "queued",
            created_at: "2026-09-21T08:00:00Z",
            started_at: null,
            finished_at: null,
        }];
        const fetchMock = vi.fn(async () => new Response(JSON.stringify(items), {
            status: 200,
            headers: { "Content-Type": "application/json" },
        }));
        vi.stubGlobal("fetch", fetchMock);

        expect(await listSlideJobs()).toEqual(items);
        expect(fetchMock).toHaveBeenCalledWith(
            "/api/v1/slides/jobs?limit=20",
            expect.objectContaining({ headers: expect.any(Headers) }),
        );
    });
});
