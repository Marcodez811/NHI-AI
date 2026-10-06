"use client";

import { useCallback, useEffect, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import {
    fetchAppSettings,
    resetAppSetting,
    updateAppSetting,
    type AppSetting,
    type AppSettingGroup,
    type AppSettingValue,
    type AppSettingsResponse,
} from "@/lib/api/appSettings";
import { fetchCatalogModels, type CatalogModel } from "@/lib/api/config";
import { SettingRow } from "./SettingRow";

/** Fetches and edits settings; renders `level` groups, with `before` shown above the groups. */
export function useAppSettings() {
    const [data, setData] = useState<AppSettingsResponse | null>(null);
    const [models, setModels] = useState<CatalogModel[]>([]);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [errors, setErrors] = useState<Record<string, string>>({});

    const reload = useCallback(async () => {
        try {
            const [settings, catalog] = await Promise.all([fetchAppSettings(), fetchCatalogModels("chat").catch(() => [])]);
            setData(settings);
            setModels(catalog);
            setLoadError(null);
        } catch (error) {
            setLoadError(error instanceof Error ? error.message : "無法載入設定。");
        }
    }, []);

    useEffect(() => {
        void reload();
    }, [reload]);

    const replace = (updated: AppSetting) =>
        setData((current) =>
            current && {
                ...current,
                groups: current.groups.map((group) => ({
                    ...group,
                    settings: group.settings.map((item) => (item.key === updated.key ? updated : item)),
                })),
            },
        );

    const save = async (key: string, value: AppSettingValue) => {
        try {
            replace(await updateAppSetting(key, value));
            setErrors((current) => ({ ...current, [key]: "" }));
            // Related settings (default vs hidden models) may have changed; refetch quietly.
            void reload();
        } catch (error) {
            setErrors((current) => ({ ...current, [key]: error instanceof Error ? error.message : "儲存失敗。" }));
        }
    };

    const reset = async (key: string) => {
        try {
            replace(await resetAppSetting(key));
            setErrors((current) => ({ ...current, [key]: "" }));
            void reload();
        } catch (error) {
            setErrors((current) => ({ ...current, [key]: error instanceof Error ? error.message : "重設失敗。" }));
        }
    };

    return { data, models, loadError, errors, save, reset };
}

export type AppSettingsState = ReturnType<typeof useAppSettings>;

export function SettingsPanel({ state, level }: { state: AppSettingsState; level: "user" | "dev" }) {
    const { data, models, loadError, errors, save, reset } = state;
    if (loadError) return <p role="alert" className="text-sm text-destructive">{loadError}</p>;
    if (!data) return <Skeleton className="h-24 w-full" />;
    const hidden = new Set(
        data.groups.flatMap((group) => group.settings).find((item) => item.key === "chat.hidden_models")?.value as string[] | undefined,
    );
    const groups: AppSettingGroup[] = data.groups.filter((group) => group.level === level);
    return (
        <div className="space-y-8">
            {groups.map((group) => (
                <section key={group.id} aria-labelledby={`group-${group.id}`} className="space-y-1">
                    <h2 id={`group-${group.id}`} className="text-base font-semibold">{group.label_zh}</h2>
                    <div>
                        {group.settings.map((setting) => (
                            <SettingRow
                                key={setting.key}
                                setting={setting}
                                models={setting.type === "model" ? models.filter((model) => !hidden.has(model.id)) : models}
                                error={errors[setting.key] || undefined}
                                onSave={save}
                                onReset={reset}
                            />
                        ))}
                    </div>
                </section>
            ))}
        </div>
    );
}
