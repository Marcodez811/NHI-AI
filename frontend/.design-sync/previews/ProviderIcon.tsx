import { ProviderIcon } from "nhi-ai-ui";

export const Providers = () => (
  <div className="flex items-center gap-4 text-sm">
    <span className="flex items-center gap-1.5"><ProviderIcon provider="openai" className="size-5" />OpenAI</span>
    <span className="flex items-center gap-1.5"><ProviderIcon provider="anthropic" className="size-5" />Anthropic</span>
    <span className="flex items-center gap-1.5"><ProviderIcon provider="gemini" className="size-5" />Google Gemini</span>
  </div>
);

export const Unavailable = () => (
  <div className="flex items-center gap-1.5 text-sm text-muted-foreground"><ProviderIcon provider="gemini" className="size-5" disabled />Gemini（未設定金鑰）</div>
);
