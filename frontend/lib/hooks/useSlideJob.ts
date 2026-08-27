"use client";

import {
    CreateSlidePayload,
    CreateSlidesJobResponse,
    createSlideJob,
    getSlideJob,
    SlideJob,
} from "../api";
import {
    AgentJobClientPhase,
    useAgentJob,
    UseAgentJobResult,
} from "./useAgentJob";

export type SlideJobPhase = AgentJobClientPhase;

export interface UseSlideJobOptions {
    /** Delay between healthy polls. Defaults to two seconds. */
    pollIntervalMs?: number;
    /** Maximum delay after transient poll failures. Defaults to ten seconds. */
    maxPollIntervalMs?: number;
    /** Override only for isolated consumers; the default survives refreshes. */
    storageKey?: string;
}

export type UseSlideJobResult = UseAgentJobResult<
    CreateSlidePayload,
    SlideJob,
    CreateSlidesJobResponse
>;

const SLIDE_JOB_STORAGE_KEY = "nhi-ai:active-slide-job-id";

/** Slides adapter for the workflow-neutral agent job lifecycle hook. */
export function useSlideJob(options: UseSlideJobOptions = {}): UseSlideJobResult {
    return useAgentJob<CreateSlidePayload, SlideJob, CreateSlidesJobResponse>({
        createJob: createSlideJob,
        getJob: getSlideJob,
        pollIntervalMs: options.pollIntervalMs,
        maxPollIntervalMs: options.maxPollIntervalMs,
        storageKey: options.storageKey ?? SLIDE_JOB_STORAGE_KEY,
    });
}
