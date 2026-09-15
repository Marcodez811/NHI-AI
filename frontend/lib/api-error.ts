import { ApiError, getApiErrorMessage } from "./api/client";

/** Normalize unknown transport failures without discarding existing API metadata. */
export function asApiError(error: unknown, fallback: string): ApiError {
    if (error instanceof ApiError) return error;
    if (error instanceof Error && error.message) {
        return new ApiError(0, error.message);
    }
    return new ApiError(0, getApiErrorMessage(error, fallback));
}
