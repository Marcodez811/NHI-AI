/** Domain API entrypoint. The legacy ../api barrel remains source-compatible. */
export * from "./documents";
export * from "./chat";
export * from "./slides";
export * from "./agents";
export * from "./retrieval";
export { ApiError, getApiErrorMessage } from "./client";
