import type { Citation } from "../api/chat";

export type View = "chat" | "files" | "slides" | "workflows";

export type ChatMessage = {
    role: "user" | "assistant";
    text: string;
    citations?: Citation[];
};

export type Tone = "formal" | "casual";
