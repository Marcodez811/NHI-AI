"use client";

import { createNewsJob, getNewsJob, type CreateNewsJobResponse, type CreateNewsPayload, type NewsJob } from "../api";
import { useAgentJob } from "./useAgentJob";

export function useNewsJob() {
    return useAgentJob<CreateNewsPayload, NewsJob, CreateNewsJobResponse>({
        createJob: createNewsJob,
        getJob: getNewsJob,
        storageKey: "nhi-ai:active-news-job-id",
    });
}
