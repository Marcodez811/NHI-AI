/** Slide-generation transport contracts. */
export {
  MAX_DOCUMENTS,
  SUPPORTED_SLIDE_EXTENSIONS,
  supportsSlideGeneration,
  createSlideJob,
  getSlideJob,
  listSlideJobs,
  getSlideJobOutline,
  streamSlideJobOutlineMessage,
  approveSlideJobOutline,
  slideDownloadUrl,
} from "../api";

export type {
  DocumentRead,
  AgentJobPhase,
  CreateSlidesJobResponse,
  CreateSlidePayload,
  SlideJob,
  SlideJobSummary,
  SlideOutline,
  SlideOutlineNode,
  OutlineEmphasis,
  OutlineRevisionResponse,
  ApproveOutlineResponse,
} from "../api";
