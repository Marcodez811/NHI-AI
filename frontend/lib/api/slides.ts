/** Slide-generation transport contracts. */
export {
  MAX_DOCUMENTS,
  SUPPORTED_SLIDE_EXTENSIONS,
  supportsSlideGeneration,
  createSlideJob,
  getSlideJob,
  getSlideDownloadUrl,
  slideDownloadUrl,
} from "../api";

export type {
  DocumentRead,
  AgentJobPhase,
  SlideJobStatus,
  CreateSlidesJobResponse,
  CreateSlidePayload,
  SlidesJobStatusResponse,
  SlideJob,
} from "../api";
