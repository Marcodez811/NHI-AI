export type Category = "legislative_qa" | "public_opinion" | "bei_can";
export const CATEGORY_LABELS: Record<Category, string> = { legislative_qa: "立院諮詢", public_opinion: "輿情", bei_can: "備參" };
export type DocumentStatus = "queued" | "indexing" | "processing" | "ready" | "failed" | "deleting" | string;
export type DocumentRecord = { id: string; filename: string; display_name?: string; mime_type?: string; size_bytes?: number; checksum?: string; category: Category; folder_id?: string | null; folder_name?: string | null; status: DocumentStatus; created_at?: string; updated_at?: string; ingestion_stage?: string | null; ingestion_error?: string | null; retrieval_enabled?: boolean };
export type Citation = { document_id?: string; filename?: string; page?: number | string; section?: string; text?: string; [key: string]: unknown };
export type SlideJob = { job_id: string; status: "queued" | "running" | "completed" | "failed"; stage?: string | null; message?: string | null; error?: string | null; download_url?: string | null };

const API_ROOT = "/api/v1";
export class ApiError extends Error { constructor(public status: number, message: string) { super(message); this.name = "ApiError"; } }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers: { Accept: "application/json", ...(init?.headers || {}) } });
  if (!response.ok) {
    let detail = "請求失敗，請稍後再試。";
    try { const body = await response.json() as { detail?: string | { message?: string } }; detail = typeof body.detail === "string" ? body.detail : body.detail?.message || detail; } catch { /* non-json error */ }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

function normalizeDocument(raw: Record<string, unknown>): DocumentRecord {
  const folder = raw.folder as Record<string, unknown> | null | undefined;
  return { id: String(raw.id), filename: String(raw.filename || raw.original_filename || raw.display_name || raw.name || "未命名文件"), display_name: raw.display_name as string | undefined, mime_type: raw.mime_type as string | undefined, size_bytes: Number(raw.size_bytes || raw.size || 0) || undefined, checksum: raw.checksum as string | undefined, category: (raw.category || "bei_can") as Category, folder_id: (raw.folder_id || folder?.id || null) as string | null, folder_name: (raw.folder_name || folder?.name || null) as string | null, status: String(raw.status || raw.ingestion_status || "queued"), created_at: raw.created_at as string | undefined, updated_at: raw.updated_at as string | undefined, ingestion_stage: raw.ingestion_stage as string | null | undefined, ingestion_error: raw.ingestion_error as string | null | undefined, retrieval_enabled: raw.retrieval_enabled as boolean | undefined };
}

export async function fetchDocuments(): Promise<DocumentRecord[]> {
  const body = await request<unknown>("/documents");
  const rows = Array.isArray(body) ? body : (body as { items?: unknown[]; documents?: unknown[] }).items || (body as { documents?: unknown[] }).documents || [];
  return rows.filter((row): row is Record<string, unknown> => Boolean(row && typeof row === "object")).map(normalizeDocument);
}

export async function getDocument(id: string): Promise<DocumentRecord> { return normalizeDocument(await request<Record<string, unknown>>(`/documents/${encodeURIComponent(id)}`)); }
export async function uploadDocument(file: File, category: Category, folderId?: string): Promise<DocumentRecord> { const form = new FormData(); form.set("file", file); form.set("category", category); if (folderId) form.set("folder_id", folderId); return normalizeDocument(await request<Record<string, unknown>>("/documents", { method: "POST", body: form, headers: { Accept: "application/json" } })); }

export type ChatRequest = { question: string; mode: Category; document_ids?: string[] };
export type StreamHandlers = { onDelta: (text: string) => void; onDone?: (citations: Citation[]) => void };
export async function streamChat(payload: ChatRequest, handlers: StreamHandlers): Promise<void> {
  const response = await fetch(`${API_ROOT}/chat/stream`, { method: "POST", headers: { Accept: "text/event-stream", "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  if (!response.ok) { let detail = "對話服務暫時無法使用。"; try { const body = await response.json() as { detail?: string }; detail = body.detail || detail; } catch { /* ignore */ } throw new ApiError(response.status, detail); }
  if (!response.body) throw new ApiError(502, "對話串流沒有回傳內容。");
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ""; let eventName = "message"; let data = "";
  const consume = (block: string) => { for (const line of block.split("\n")) { if (line.startsWith("event:")) eventName = line.slice(6).trim(); else if (line.startsWith("data:")) data += line.slice(5).trimStart(); } if (!data) return; if (data === "[DONE]") { handlers.onDone?.([]); } else { try { const parsed = JSON.parse(data) as { type?: string; citations?: Citation[]; message?: string; text_delta?: string; delta?: string; text?: string } | Citation[]; const kind = Array.isArray(parsed) ? "done" : parsed.type || eventName; if (kind === "done") handlers.onDone?.(Array.isArray(parsed) ? parsed : parsed.citations || []); else if (kind === "error") throw new ApiError(500, Array.isArray(parsed) ? "對話服務發生錯誤。" : parsed.message || "對話服務發生錯誤。"); else { const text = Array.isArray(parsed) ? "" : parsed.text_delta ?? parsed.delta ?? (typeof parsed.text === "string" ? parsed.text : ""); if (text) handlers.onDelta(text); } } catch (error) { if (error instanceof ApiError) throw error; handlers.onDelta(data); } } data = ""; eventName = "message"; };
  while (true) { const { value, done } = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), { stream: !done }); let boundary; while ((boundary = buffer.indexOf("\n\n")) >= 0) { const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2); consume(block); } if (done) break; }
  if (buffer.trim()) consume(buffer);
}

export type CreateSlidePayload = { title: string; document_ids: string[]; slides_count: number; guidance: string; tone: "formal" | "casual" };
export async function createSlideJob(payload: CreateSlidePayload): Promise<SlideJob> { return request<SlideJob>("/slides/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); }
export async function getSlideJob(id: string): Promise<SlideJob> { return request<SlideJob>(`/slides/jobs/${encodeURIComponent(id)}`); }
