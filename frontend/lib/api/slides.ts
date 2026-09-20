/** Slide-generation transport contracts. */
export {
  MAX_DOCUMENTS,
  SUPPORTED_SLIDE_EXTENSIONS,
  supportsSlideGeneration,
  createSlideJob,
  getSlideJob,
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
  SlideOutline,
  SlideOutlineNode,
  OutlineEmphasis,
  OutlineRevisionResponse,
  ApproveOutlineResponse,
} from "../api";
