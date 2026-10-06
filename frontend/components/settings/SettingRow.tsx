"use client";

import { useState } from "react";

import { ChatModelPicker } from "@/components/chat/ChatModelPicker";
import { ProviderIcon, groupByProvider, providerLabel } from "@/components/chat/ProviderIcon";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type { AppSetting, AppSettingSource, AppSettingValue } from "@/lib/api/appSettings";
import type { CatalogModel } from "@/lib/api/config";

export const SOURCE_BADGES: Record<AppSettingSource, string> = {
    database: "自訂",
    env: ".env",
    config: "設定檔",
    default: "預設",
};

export interface SettingRowProps {
    setting: AppSetting;
    models: CatalogModel[];
    onSave: (key: string, value: AppSettingValue) => Promise<void>;
    onReset: (key: string) => Promise<void>;
    error?: string;
}

/** One editable setting: label, control by type, source badge, reset. Saves on change. */
export function SettingRow({ setting, models, onSave, onReset, error }: SettingRowProps) {
    const [confirming, setConfirming] = useState(false);
    const [draft, setDraft] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const id = `setting-${setting.key}`;

    const save = async (value: AppSettingValue) => {
        setBusy(true);
        try {
            await onSave(setting.key, value);
        } finally {
            setBusy(false);
        }
    };

    const commitNumber = () => {
        if (draft === null) return;
        const text = draft.trim();
        setDraft(null);
        if (text === "" || Number.isNaN(Number(text)) || Number(text) === setting.value) return;
        void save(Number(text));
    };

    let control: React.ReactNode = null;
    if (setting.type === "bool") {
        control = (
            <Switch
                aria-labelledby={`${id}-label`}
                checked={setting.value === true}
                disabled={busy}
                onCheckedChange={(checked) => void save(checked)}
            />
        );
    } else if (setting.type === "int" || setting.type === "float") {
        control = (
            <div className="flex items-center gap-2">
                <Input
                    id={id}
                    type="number"
                    inputMode="decimal"
                    className="w-full sm:w-40"
                    aria-labelledby={`${id}-label`}
                    aria-invalid={error ? true : undefined}
                    min={setting.constraints.min}
                    max={setting.constraints.max}
                    step={setting.type === "int" ? 1 : "any"}
                    value={draft ?? String(setting.value)}
                    disabled={busy}
                    onChange={(event) => setDraft(event.target.value)}
                    onBlur={commitNumber}
                    onKeyDown={(event) => {
                        if (event.key === "Enter") commitNumber();
                    }}
                />
                {setting.unit_zh && <span className="shrink-0 text-sm text-muted-foreground">{setting.unit_zh}</span>}
            </div>
        );
    } else if (setting.type === "model") {
        control = (
            <ChatModelPicker
                models={models}
                value={String(setting.value)}
                disabled={busy}
                onValueChange={(next) => void save(next)}
            />
        );
    } else if (setting.type === "model_list") {
        // Stored as the hidden ids; shown as "visible" switches, which reads more naturally.
        const hiddenIds = Array.isArray(setting.value) ? setting.value : [];
        control = (
            <div className="w-full space-y-4" aria-labelledby={`${id}-label`} role="group">
                {groupByProvider(models).map((group) => (
                    <div key={group.provider} className="space-y-1.5">
                        <div className="text-xs font-medium text-muted-foreground">{providerLabel(group.provider)}</div>
                        <ul className="divide-y divide-border rounded-lg border border-border">
                            {group.models.map((model) => {
                                const visible = !hiddenIds.includes(model.id);
                                return (
                                    <li key={model.id}>
                                        <label className="flex min-h-11 items-center gap-2.5 px-3 text-sm">
                                            <ProviderIcon provider={model.provider} disabled={!model.available} />
                                            <span className="min-w-0 flex-1 truncate">{model.label}</span>
                                            {!model.available && <span className="shrink-0 text-xs text-muted-foreground">未設定金鑰</span>}
                                            <Switch
                                                size="sm"
                                                checked={visible}
                                                disabled={busy}
                                                onCheckedChange={(show) =>
                                                    void save(show ? hiddenIds.filter((value) => value !== model.id) : [...hiddenIds, model.id])
                                                }
                                            />
                                        </label>
                                    </li>
                                );
                            })}
                        </ul>
                    </div>
                ))}
            </div>
        );
    }
    // Lists need the full width; single controls sit to the right of the label.
    const stacked = setting.type === "model_list";

    return (
        <div
            className={
                stacked
                    ? "flex flex-col gap-3 border-b border-border py-4 last:border-b-0"
                    : "flex flex-col gap-3 border-b border-border py-4 last:border-b-0 sm:flex-row sm:items-start sm:justify-between sm:gap-6"
            }
        >
            <div className="min-w-0 space-y-1 sm:max-w-sm">
                <div className="flex flex-wrap items-center gap-2">
                    <span id={`${id}-label`} className="text-sm font-medium">{setting.label_zh}</span>
                    <Badge variant="secondary" data-source={setting.source}>{SOURCE_BADGES[setting.source]}</Badge>
                    {setting.restart_required && <Badge variant="outline">需重新啟動</Badge>}
                </div>
                <p className="text-sm leading-6 text-muted-foreground">{setting.description_zh}</p>
                {setting.restart_required && (
                    <p className="text-xs text-muted-foreground">此設定需重新啟動服務後才會生效。</p>
                )}
                {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
            </div>
            <div className={stacked ? "flex min-w-0 flex-col items-start gap-2" : "flex min-w-0 flex-col items-start gap-2 sm:items-end"}>
                {control}
                {setting.source === "database" && (
                    <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => setConfirming(true)}>
                        重設為預設
                    </Button>
                )}
            </div>
            <ConfirmDialog
                open={confirming}
                title={`重設「${setting.label_zh}」？`}
                description="自訂的值會被移除，改回 .env、設定檔或系統預設的值。"
                confirmLabel="重設"
                onCancel={() => setConfirming(false)}
                onConfirm={() => {
                    setConfirming(false);
                    setBusy(true);
                    void onReset(setting.key).finally(() => setBusy(false));
                }}
            />
        </div>
    );
}
