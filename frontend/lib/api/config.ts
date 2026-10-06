/** Product configuration served by the backend (config.yaml). */
import { request } from "./client";

export interface ChatLimits {
    max_attachments: number;
    max_message_chars: number;
    max_document_bytes: number;
    max_pdf_bytes: number;
    max_image_bytes: number;
}

export interface ClientConfig {
    chat: ChatLimits;
    max_upload_bytes: number;
}

/** Used until the server answers (or if it cannot), so the composer never blocks. */
export const DEFAULT_CLIENT_CONFIG: ClientConfig = {
    chat: {
        max_attachments: 10,
        max_message_chars: 20_000,
        max_document_bytes: 25 * 1024 * 1024,
        max_pdf_bytes: 20 * 1024 * 1024,
        max_image_bytes: 10 * 1024 * 1024,
    },
    max_upload_bytes: 250 * 1024 * 1024,
};

export type ModelUse = "chat" | "agents" | "codex";

export interface CatalogModel {
    id: string;
    label: string;
    provider: "openai" | "anthropic" | "gemini" | string;
    available: boolean;
}

export async function fetchClientConfig(options: { signal?: AbortSignal } = {}): Promise<ClientConfig> {
    return request<ClientConfig>("/config/client", options);
}

export async function fetchCatalogModels(
    use: ModelUse,
    options: { signal?: AbortSignal } = {},
): Promise<CatalogModel[]> {
    return request<CatalogModel[]>(`/models?use=${use}`, options);
}
