/** Slide-generation transport contracts. */
export {
  MAX_DOCUMENTS,
  SUPPORTED_SLIDE_EXTENSIONS,
  supportsSlideGeneration,
  createSlideJob,
  getSlideJob,
  slideDownloadUrl,
} from "../api";

export type {
  DocumentRead,
  AgentJobPhase,
  CreateSlidesJobResponse,
  CreateSlidePayload,
  SlideJob,
} from "../api";
