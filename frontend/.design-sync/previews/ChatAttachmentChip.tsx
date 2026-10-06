import { ChatAttachmentChip } from "nhi-ai-ui";

const created_at = "2026-10-06T09:00:00.000Z";
const doc = (name: string, source: "upload" | "knowledge_base") => ({
  id: name, display_name: name, mime_type: "application/pdf", kind: "document", size_bytes: 482113,
  status: "ready", error: null, text_chars: 18240, created_at, source,
});

export const States = () => (
  <div className="flex flex-wrap gap-2">
    <ChatAttachmentChip sessionId={null} onRemove={() => {}} item={{ localId: "a", status: "ready", attachment: doc("114年第1季醫院總額執行報告.pdf", "upload") } as never} />
    <ChatAttachmentChip sessionId={null} onRemove={() => {}} item={{ localId: "b", status: "ready", attachment: doc("個別醫院前瞻式預算分區共管試辦計畫.docx", "knowledge_base") } as never} />
    <ChatAttachmentChip sessionId={null} onRemove={() => {}} item={{ localId: "c", status: "uploading", attachment: doc("急診照護品質方案.pdf", "upload") } as never} />
    <ChatAttachmentChip sessionId={null} onRemove={() => {}} item={{ localId: "d", status: "failed", error: "檔案超過大小上限（20 MB）。", attachment: doc("三高防治年度報告.pdf", "upload") } as never} />
  </div>
);
