import { useState } from "react";
import { ChatComposer, ChatEmptyState } from "nhi-ai-ui";

const models = [{ id: "gpt-6-luna", label: "GPT-6 Luna", provider: "openai", available: true }];

export const NewChat = () => {
  const [draft, setDraft] = useState("");
  return (
    <div className="py-10">
      <ChatEmptyState onPick={setDraft}>
        <ChatComposer
          sessionId={null} draft={draft} setDraft={setDraft} attachments={[]}
          onAttachFiles={() => {}} onRemoveAttachment={() => {}}
          models={models} model="gpt-6-luna" setModel={() => {}} modelsLoading={false}
          busy={false} uploadsPending={false} onSend={() => {}} onStop={() => {}}
        />
      </ChatEmptyState>
    </div>
  );
};
