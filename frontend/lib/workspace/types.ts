import type { ChatStatusPhase, Citation } from "../api/chat";

export type View = "chat" | "files" | "slides" | "workflows";

export type ChatMessage = {
    id?: string;
    role: "user" | "assistant";
    text: string;
    citations?: Citation[];
    status?: ChatStatusPhase;
};

export type Tone = "formal" | "casual";
