import type { Citation } from "../api";

export type View = "chat" | "files" | "slides";

export type ChatMessage = {
    role: "user" | "assistant";
    text: string;
    citations?: Citation[];
};

export type Tone = "formal" | "casual";
