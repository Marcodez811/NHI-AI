"use client";

import {
    CreateSlidePayload,
    CreateSlidesJobResponse,
    createSlideJob,
    getSlideJob,
    SlideJob,
} from "../api/slides";
import { useAgentJob, UseAgentJobResult } from "./useAgentJob";

export interface UseSlideJobOptions {
    /** Delay between healthy polls. Defaults to two seconds. */
    pollIntervalMs?: number;
    /** Maximum delay after transient poll failures. Defaults to ten seconds. */
    maxPollIntervalMs?: number;
    /** The job page supplies this from the URL; the index has no active job. */
    jobId?: string;
}

export type UseSlideJobResult = UseAgentJobResult<
    CreateSlidePayload,
    SlideJob,
    CreateSlidesJobResponse
>;

/** Slides adapter for the workflow-neutral agent job lifecycle hook. */
export function useSlideJob(options: UseSlideJobOptions = {}): UseSlideJobResult {
    return useAgentJob<CreateSlidePayload, SlideJob, CreateSlidesJobResponse>({
        createJob: createSlideJob,
        getJob: getSlideJob,
        pollIntervalMs: options.pollIntervalMs,
        maxPollIntervalMs: options.maxPollIntervalMs,
        // Scoped to this one job id, never a shared "active job" slot: the URL
        // still decides which job is shown, this only lets that job's own
        // phase history (and so its revision count) survive a refresh. No id
        // (the create form) means nothing to key by, so persistence is
        // skipped there.
        storageKey: options.jobId ? `nhi-ai:slides-job:${options.jobId}` : null,
        initialJobId: options.jobId,
    });
}
