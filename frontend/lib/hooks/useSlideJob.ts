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
        storageKey: null,
        initialJobId: options.jobId,
    });
}
