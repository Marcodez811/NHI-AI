/** Per-workflow agent model settings. */
import { request } from "./client";

export type AgentSettingStage = "extraction" | "planner" | "author" | "reviewer";
export type AgentSettingWorkflow = "slides" | "news";
export type AgentReasoningEffort = "none" | "low" | "medium" | "high" | "xhigh" | "max";

export interface StoredAgentStageSettings {
    runner: string | null;
    model: string | null;
    reasoning_effort: AgentReasoningEffort | null;
    planner_enabled: boolean | null;
}

export interface EffectiveAgentSetting<T> {
    value: T;
    source: "stored" | "env" | "default" | string;
}

export interface AgentStageSettings {
    stored: StoredAgentStageSettings;
    effective: {
        runner: EffectiveAgentSetting<string>;
        model: EffectiveAgentSetting<string | null>;
        reasoning_effort: EffectiveAgentSetting<AgentReasoningEffort | null>;
        planner_enabled: EffectiveAgentSetting<boolean>;
    };
    allowed_runners: string[];
}

export interface AgentSettingsResponse {
    stages: Partial<Record<AgentSettingStage, AgentStageSettings>>;
    providers: {
        openai: boolean;
        gemini: boolean;
        anthropic: boolean;
    };
}

export async function fetchAgentSettings(
    workflow: AgentSettingWorkflow,
    options: { signal?: AbortSignal } = {},
): Promise<AgentSettingsResponse> {
    return request<AgentSettingsResponse>(`/agent-settings/${workflow}`, options);
}

export async function updateAgentStageSettings(
    workflow: AgentSettingWorkflow,
    stage: AgentSettingStage,
    settings: StoredAgentStageSettings,
): Promise<AgentSettingsResponse> {
    return request<AgentSettingsResponse>(`/agent-settings/${workflow}/${stage}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
    });
}

