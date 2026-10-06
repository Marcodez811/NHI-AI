/** Editable settings (settings page): list, override, reset. */
import { request } from "./client";

export type AppSettingType = "int" | "float" | "bool" | "enum" | "model" | "model_list";
export type AppSettingSource = "database" | "env" | "config" | "default";
export type AppSettingValue = number | boolean | string | string[];

export interface AppSettingConstraints {
    min?: number;
    max?: number;
    choices?: string[];
}

export interface AppSetting {
    key: string;
    label_zh: string;
    description_zh: string;
    type: AppSettingType;
    constraints: AppSettingConstraints;
    unit_zh: string | null;
    value: AppSettingValue;
    default_value: AppSettingValue;
    source: AppSettingSource;
    restart_required: boolean;
    level: "user" | "dev";
}

export interface AppSettingGroup {
    id: string;
    label_zh: string;
    level: "user" | "dev";
    settings: AppSetting[];
}

export interface AppSettingsResponse {
    dev_enabled: boolean;
    groups: AppSettingGroup[];
}

export async function fetchAppSettings(options: { signal?: AbortSignal } = {}): Promise<AppSettingsResponse> {
    return request<AppSettingsResponse>("/app-settings", options);
}

export async function updateAppSetting(key: string, value: AppSettingValue): Promise<AppSetting> {
    return request<AppSetting>(`/app-settings/${key}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
    });
}

export async function resetAppSetting(key: string): Promise<AppSetting> {
    return request<AppSetting>(`/app-settings/${key}`, { method: "DELETE" });
}
