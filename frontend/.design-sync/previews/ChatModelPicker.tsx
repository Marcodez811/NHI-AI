import { useState } from "react";
import { ChatModelPicker } from "nhi-ai-ui";

const models = [
  { id: "gpt-6-luna", label: "GPT-6 Luna", provider: "openai", available: true },
  { id: "claude-sonnet-5-5", label: "Claude Sonnet 5.5", provider: "anthropic", available: true },
  { id: "gemini-3-pro", label: "Gemini 3 Pro", provider: "gemini", available: false },
];

function Picker({ initial }: { initial: string }) {
  const [value, setValue] = useState(initial);
  return <ChatModelPicker models={models} value={value} onValueChange={setValue} />;
}

export const Providers = () => (
  <div className="flex flex-wrap items-center gap-4">
    <Picker initial="gpt-6-luna" />
    <Picker initial="claude-sonnet-5-5" />
  </div>
);

export const Loading = () => <ChatModelPicker models={models} value="gpt-6-luna" onValueChange={() => {}} disabled />;
