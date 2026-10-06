import { useState } from "react";
import { ChatComposer } from "nhi-ai-ui";

const models = [
  { id: "gpt-6-luna", label: "GPT-6 Luna", provider: "openai", available: true },
  { id: "claude-sonnet-5-5", label: "Claude Sonnet 5.5", provider: "anthropic", available: true },
];
const created_at = "2026-10-06T09:00:00.000Z";

function Composer({ initialDraft = "", attachments = [], busy = false }: { initialDraft?: string; attachments?: unknown[]; busy?: boolean }) {
  const [draft, setDraft] = useState(initialDraft);
  const [model, setModel] = useState("gpt-6-luna");
  return (
    <div className="mx-auto max-w-2xl">
      <ChatComposer
        sessionId={null} draft={draft} setDraft={setDraft} attachments={attachments as never}
        onAttachFiles={() => {}} onRemoveAttachment={() => {}}
        models={models} model={model} setModel={setModel} modelsLoading={false}
        busy={busy} uploadsPending={false} onSend={() => {}} onStop={() => {}}
      />
    </div>
  );
}

export const Empty = () => <Composer />;

export const WithDraftAndAttachments = () => (
  <Composer
    initialDraft="請比較附件中 113 與 114 年第 1 季的分區點值。"
    attachments={[
      { localId: "a", status: "ready", attachment: { id: "a", display_name: "114年第1季醫院總額執行報告.pdf", mime_type: "application/pdf", kind: "document", size_bytes: 482113, status: "ready", error: null, text_chars: 18240, created_at, source: "upload" } },
      { localId: "b", status: "ready", attachment: { id: "b", display_name: "個別醫院前瞻式預算分區共管試辦計畫.docx", mime_type: "application/pdf", kind: "document", size_bytes: 96210, status: "ready", error: null, text_chars: 9120, created_at, source: "knowledge_base" } },
    ]}
  />
);

export const Responding = () => <Composer initialDraft="" busy />;
