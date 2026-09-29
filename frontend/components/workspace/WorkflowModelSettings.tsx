"use client";

import { useEffect, useState } from "react";
import {
    fetchAgentSettings,
    updateAgentStageSettings,
    type AgentReasoningEffort,
    type AgentSettingStage,
    type AgentSettingsResponse,
    type AgentSettingWorkflow,
    type StoredAgentStageSettings,
} from "../../lib/api/settings";
import { ApiError } from "../../lib/api/client";
import { Button } from "../ui/button";
import { Input } from "../ui/input";

const STAGE_LABELS: Record<AgentSettingStage, string> = {
    extraction: "資料擷取",
    planner: "工作規劃",
    author: "內容撰寫",
    reviewer: "內容審查",
};

// Pipeline order. Each workflow declares its own subset of these stages in its
// backend adapter, and the API returns only those, so the response decides
// which stages appear here.
const STAGE_ORDER = Object.keys(STAGE_LABELS) as AgentSettingStage[];

function workflowStages(settings: AgentSettingsResponse | null): AgentSettingStage[] {
    return settings ? STAGE_ORDER.filter((id) => settings.stages[id] !== undefined) : [];
}

const EFFORTS: { value: AgentReasoningEffort; label: string }[] = [
    { value: "none", label: "無" },
    { value: "low", label: "低" },
    { value: "medium", label: "中" },
    { value: "high", label: "高" },
    { value: "xhigh", label: "極高" },
    { value: "max", label: "最高" },
];

const RUNNER_LABELS: Record<string, string> = {
    codex: "Codex",
    openai: "OpenAI",
    gemini: "Gemini",
    anthropic: "Anthropic",
};
const SOURCE_LABELS: Record<string, string> = {
    database: "來自自訂設定",
    env: "來自 .env",
    default: "來自系統預設",
};
const OPENAI_MODELS = ["gpt-6-astra", "gpt-6-sol", "gpt-6-luna"];
// Codex takes plain OpenAI model names; the Agents runner takes LiteLLM names.
const MODEL_SUGGESTIONS: Record<string, string[]> = {
    codex: OPENAI_MODELS,
    agents: ["litellm/gemini/gemini-3.8-flash", ...OPENAI_MODELS.map((model) => `litellm/openai/${model}`)],
};
const EMPTY_SETTINGS: StoredAgentStageSettings = {
    runner: null,
    model: null,
    reasoning_effort: null,
    planner_enabled: null,
};

function storedSettings(settings: AgentSettingsResponse): Record<AgentSettingStage, StoredAgentStageSettings> {
    return Object.fromEntries(
        workflowStages(settings).map((id) => [id, { ...(settings.stages[id]?.stored ?? EMPTY_SETTINGS) }]),
    ) as Record<AgentSettingStage, StoredAgentStageSettings>;
}

function runnerLabel(value: string): string {
    return RUNNER_LABELS[value] ?? "其他執行器";
}

function sourceLabel(value: string): string {
    return SOURCE_LABELS[value] ?? "來自系統設定";
}

function effectiveValue(value: string | boolean | null): string {
    if (value === null) return "未設定";
    if (typeof value === "boolean") return value ? "已啟用" : "未啟用";
    return value;
}

export function WorkflowModelSettings({ workflow }: { workflow: AgentSettingWorkflow }) {
    const [data, setData] = useState<AgentSettingsResponse | null>(null);
    const stages = workflowStages(data);
    const [drafts, setDrafts] = useState<Record<AgentSettingStage, StoredAgentStageSettings> | null>(null);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState(false);
    const [saving, setSaving] = useState<AgentSettingStage | null>(null);
    const [feedback, setFeedback] = useState<Partial<Record<AgentSettingStage, "saved" | "error">>>({});
    const [errorMessages, setErrorMessages] = useState<Partial<Record<AgentSettingStage, string>>>({});

    useEffect(() => {
        const controller = new AbortController();
        setData(null);
        setDrafts(null);
        setLoading(true);
        setLoadError(false);
        fetchAgentSettings(workflow, { signal: controller.signal })
            .then((settings) => {
                setData(settings);
                setDrafts(storedSettings(settings));
            })
            .catch(() => {
                if (!controller.signal.aborted) setLoadError(true);
            })
            .finally(() => {
                if (!controller.signal.aborted) setLoading(false);
            });
        return () => controller.abort();
    }, [workflow]);

    function change(stage: AgentSettingStage, key: keyof StoredAgentStageSettings, value: string | boolean | null) {
        if (!drafts) return;
        setDrafts({
            ...drafts,
            [stage]: { ...drafts[stage], [key]: value },
        });
        setFeedback((current) => ({ ...current, [stage]: undefined }));
        setErrorMessages((current) => ({ ...current, [stage]: undefined }));
    }

    async function save(stage: AgentSettingStage, values = drafts?.[stage], refreshAfterSave = false) {
        if (!drafts || !values) return;
        setSaving(stage);
        setFeedback((current) => ({ ...current, [stage]: undefined }));
        setErrorMessages((current) => ({ ...current, [stage]: undefined }));
        try {
            const saved = await updateAgentStageSettings(workflow, stage, values);
            const result = refreshAfterSave ? await fetchAgentSettings(workflow) : saved;
            setData(result);
            setDrafts(storedSettings(result));
            setFeedback((current) => ({ ...current, [stage]: "saved" }));
        } catch (error) {
            setFeedback((current) => ({ ...current, [stage]: "error" }));
            if (error instanceof ApiError) {
                setErrorMessages((current) => ({ ...current, [stage]: error.message }));
            } else {
                setErrorMessages((current) => ({ ...current, [stage]: "儲存失敗，請稍後再試。" }));
            }
        } finally {
            setSaving(null);
        }
    }

    function restore(stage: AgentSettingStage) {
        if (!drafts) return;
        setDrafts({ ...drafts, [stage]: { ...EMPTY_SETTINGS } });
        void save(stage, { ...EMPTY_SETTINGS }, true);
    }

    return (
        <div className="w-full py-8">
            <section aria-labelledby="provider-status-title" className="mb-8 rounded-xl border border-border bg-card p-5">
                <div className="mb-4">
                    <h2 id="provider-status-title" className="font-semibold">服務提供者狀態</h2>
                    <p className="mt-1 text-sm text-muted-foreground">僅顯示金鑰是否已設定，不會顯示或傳送金鑰內容。</p>
                </div>
                {loading ? <p className="text-sm text-muted-foreground">正在載入設定…</p> : loadError || !data ? (
                    <p role="alert" className="text-sm text-destructive">無法載入設定，請稍後再試。</p>
                ) : (
                    <ul className="grid gap-3 sm:grid-cols-3">
                        {([
                            ["openai", "OpenAI"],
                            ["gemini", "Gemini"],
                            ["anthropic", "Anthropic"],
                        ] as const).map(([provider, label]) => (
                            <li key={provider} className="flex items-center justify-between rounded-lg bg-muted/50 px-4 py-3 text-sm">
                                <span>{label}</span>
                                <span
                                    aria-label={`${label}${data.providers[provider] ? "已設定" : "未設定"}`}
                                    className={data.providers[provider] ? "inline-flex items-center gap-1 font-medium text-foreground" : "inline-flex items-center gap-1 text-muted-foreground"}
                                >
                                    <span aria-hidden="true">{data.providers[provider] ? "✓" : "✗"}</span>
                                    {data.providers[provider] ? "已設定" : "未設定"}
                                </span>
                            </li>
                        ))}
                    </ul>
                )}
            </section>

            {loadError && (
                <div className="mb-6 flex items-center justify-between rounded-xl border border-destructive/30 bg-destructive/5 p-4">
                    <p className="text-sm text-destructive">無法載入模型設定，請確認連線後重試。</p>
                    <Button type="button" variant="outline" onClick={() => window.location.reload()}>重新載入</Button>
                </div>
            )}

            <div className="space-y-5">
                {stages.map((id) => {
                    const label = STAGE_LABELS[id];
                    const stage = data?.stages[id];
                    const draft = drafts?.[id];
                    const planner = id === "planner";
                    return (
                        <section key={id} aria-labelledby={`stage-${id}`} className="rounded-xl border border-border bg-card p-5 sm:p-6">
                            <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
                                <div>
                                    <h2 id={`stage-${id}`} className="text-lg font-semibold">{label}</h2>
                                    <p className="mt-1 text-sm text-muted-foreground">指定此階段的執行模型；留白則使用環境或系統預設值。</p>
                                </div>
                                {planner && draft && (
                                    <label className="inline-flex min-h-10 cursor-pointer items-center gap-3 rounded-lg border border-border px-3 text-sm">
                                        <input
                                            type="checkbox"
                                            checked={draft.planner_enabled ?? stage?.effective.planner_enabled.value ?? false}
                                            onChange={(event) => change(id, "planner_enabled", event.target.checked)}
                                            className="size-4 accent-primary"
                                        />
                                        啟用工作規劃
                                    </label>
                                )}
                            </div>

                            {stage && draft ? (
                                <>
                                    <div className="grid gap-4 sm:grid-cols-2">
                                        <label className="flex flex-col gap-2 text-sm font-medium">
                                            執行器
                                            <select
                                                aria-label={`${label}執行器`}
                                                value={draft.runner ?? ""}
                                                onChange={(event) => change(id, "runner", event.target.value || null)}
                                                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                                            >
                                                <option value="">使用環境設定</option>
                                                {stage.allowed_runners.map((runner) => (
                                                    <option key={runner} value={runner}>{runnerLabel(runner)}</option>
                                                ))}
                                            </select>
                                        </label>
                                        <label className="flex flex-col gap-2 text-sm font-medium">
                                            模型
                                            <Input
                                                aria-label={`${label}模型`}
                                                list={`models-${id}`}
                                                value={draft.model ?? ""}
                                                onChange={(event) => change(id, "model", event.target.value || null)}
                                                placeholder="使用環境或系統預設"
                                                className="font-normal"
                                            />
                                            <datalist id={`models-${id}`}>
                                                {(MODEL_SUGGESTIONS[draft.runner ?? stage.effective.runner.value] ?? []).map((model) => <option key={model} value={model} />)}
                                            </datalist>
                                        </label>
                                        <label className="flex flex-col gap-2 text-sm font-medium sm:col-span-2">
                                            推理程度
                                            <select
                                                aria-label={`${label}推理程度`}
                                                value={draft.reasoning_effort ?? ""}
                                                onChange={(event) => change(id, "reasoning_effort", event.target.value || null)}
                                                className="h-9 w-full rounded-lg border border-input bg-background px-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 sm:max-w-sm"
                                            >
                                                <option value="">使用環境或系統預設</option>
                                                {EFFORTS.map((effort) => <option key={effort.value} value={effort.value}>{effort.label}</option>)}
                                            </select>
                                        </label>
                                    </div>

                                    <div className="mt-5 rounded-lg bg-muted/40 px-4 py-3">
                                        <h3 className="mb-2 text-sm font-medium">目前有效設定</h3>
                                        <dl className="grid gap-3 text-sm sm:grid-cols-2">
                                            <Effective label="執行器" value={runnerLabel(stage.effective.runner.value)} source={stage.effective.runner.source} />
                                            <Effective label="模型" value={effectiveValue(stage.effective.model.value)} source={stage.effective.model.source} />
                                            <Effective
                                                label="推理程度"
                                                value={stage.effective.reasoning_effort.value ? EFFORTS.find((item) => item.value === stage.effective.reasoning_effort.value)?.label ?? String(stage.effective.reasoning_effort.value) : "未設定"}
                                                source={stage.effective.reasoning_effort.source}
                                            />
                                            {planner && <Effective label="工作規劃" value={effectiveValue(stage.effective.planner_enabled.value)} source={stage.effective.planner_enabled.source} />}
                                        </dl>
                                    </div>

                                    <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
                                        <p className="text-xs leading-5 text-muted-foreground">清除欄位即可恢復環境或系統預設值。變更只套用於之後建立的工作。</p>
                                        <div className="flex items-center gap-2">
                                            {feedback[id] === "saved" && <span role="status" className="text-sm text-muted-foreground">已儲存設定</span>}
                                            {feedback[id] === "error" && <span role="alert" className="text-sm text-destructive">{errorMessages[id] ?? "儲存失敗，請稍後再試。"}</span>}
                                            <Button type="button" variant="outline" disabled={saving === id} onClick={() => restore(id)}>
                                                恢復預設值
                                            </Button>
                                            <Button type="button" disabled={saving === id} onClick={() => void save(id)}>
                                                {saving === id ? "儲存中…" : "儲存"}
                                            </Button>
                                        </div>
                                    </div>
                                </>
                            ) : (
                                <p className="text-sm text-muted-foreground">{loading ? "正在載入設定…" : "設定暫時無法使用。"}</p>
                            )}
                        </section>
                    );
                })}
            </div>
            <p className="mt-6 text-xs text-muted-foreground">模型可用性取決於所選執行器及其環境設定；本頁不會要求或保存 API 金鑰。</p>
        </div>
    );
}

function Effective({ label, value, source }: { label: string; value: string; source: string }) {
    return (
        <div className="flex flex-col gap-1">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="flex flex-wrap items-center gap-x-2 font-medium">
                <span>{value}</span>
                <span className="text-xs font-normal text-muted-foreground">{sourceLabel(source)}</span>
            </dd>
        </div>
    );
}
