import { ChatMessageList } from "nhi-ai-ui";

const created_at = "2026-10-06T09:00:00.000Z";

const attachments = {
  "file-1": {
    id: "file-1", display_name: "114年第1季醫院總額執行報告.pdf", mime_type: "application/pdf",
    kind: "document", size_bytes: 482113, status: "ready", error: null, text_chars: 18240,
    created_at, source: "upload",
  },
  "doc-1": {
    id: "doc-1", display_name: "個別醫院前瞻式預算分區共管試辦計畫.docx",
    mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    kind: "document", size_bytes: 96210, status: "ready", error: null, text_chars: 9120,
    created_at, source: "knowledge_base",
  },
} as const;

export const Conversation = () => (
  <div className="mx-auto max-w-3xl">
    <ChatMessageList
      sessionId="demo"
      attachmentLookup={attachments as never}
      messages={[
        {
          id: "u1", localKey: "u1", role: "user", status: "complete", created_at,
          content: "請整理 114 年第 1 季各分區的平均點值，並和去年同期比較。",
          attachment_ids: ["file-1", "doc-1"],
        },
        {
          id: "a1", localKey: "a1", role: "assistant", status: "complete", created_at,
          reasoning: "先找出各分區點值，再對照 113 年第 1 季的數字。",
          reasoningDurationSeconds: 6,
          toolSteps: [
            { tool: "search_knowledge_base", label: "分區點值", done: true },
            { tool: "read_attachment", label: "114年第1季醫院總額執行報告.pdf", done: true },
          ],
          content:
            "114 年第 1 季全國平均點值為 **0.9594 元/點**，高於 113 年同期的 0.9148。\n\n" +
            "| 分區 | 114Q1 | 113Q1 |\n| --- | --- | --- |\n| 臺北 | 0.9349 | 0.8976 |\n| 北區 | 0.9519 | 0.9048 |\n| 南區 | 1.0018 | 0.9505 |\n| 東區 | 1.0168 | 0.9445 |\n\n" +
            "各分區點值皆較去年提升，其中南區、高屏與東區已超過 1 元。",
          sources: [
            { name: "114年第1季醫院總額執行報告.pdf", snippet: "全台六大分區平均點值 0.9594元/點" },
            { name: "個別醫院前瞻式預算分區共管試辦計畫說明表.docx", snippet: "五區自114年第1季上路" },
          ],
        },
      ] as never}
    />
  </div>
);

export const Thinking = () => (
  <div className="mx-auto max-w-3xl">
    <ChatMessageList
      messages={[
        { id: "u2", localKey: "u2", role: "user", status: "complete", created_at, content: "離島住診加成是從什麼時候開始的？" },
        {
          id: "a2", localKey: "a2", role: "assistant", status: "complete", created_at, content: "",
          pending: true, receivedEvent: true,
          toolSteps: [{ tool: "search_knowledge_base", label: "離島住診加成", done: false }],
        },
      ] as never}
    />
  </div>
);

export const Interrupted = () => (
  <div className="mx-auto max-w-3xl">
    <ChatMessageList
      messages={[
        { id: "u3", localKey: "u3", role: "user", status: "complete", created_at, content: "幫我列出三高防治 888 的政策目標。" },
        {
          id: "a3", localKey: "a3", role: "assistant", status: "interrupted", created_at,
          content: "三高防治 888 的目標包括：80% 三高病人接受整合性照護、80% 照護病人接受生活習慣諮商……",
        },
      ] as never}
    />
  </div>
);
