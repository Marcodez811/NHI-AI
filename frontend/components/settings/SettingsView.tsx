"use client";
import { PageContainer, PageHeader } from "@/components/app-shell/page-layout";

import { useState } from "react";

import { AppearanceSetting } from "@/components/settings/AppearanceSetting";
import { SettingsPanel, useAppSettings } from "@/components/settings/SettingsPanel";
import { cn } from "@/lib/utils";

type Tab = "general" | "dev";

export function SettingsView() {
    const state = useAppSettings();
    const [tab, setTab] = useState<Tab>("general");
    const devEnabled = state.data?.dev_enabled === true;
    const active: Tab = devEnabled ? tab : "general";
    const tabs: { id: Tab; label: string }[] = [{ id: "general", label: "一般" }, ...(devEnabled ? [{ id: "dev" as const, label: "開發者" }] : [])];

    return (
        <PageContainer className="space-y-10">
            <PageHeader title="設定" />
            {tabs.length > 1 && (
            <div role="tablist" aria-label="設定分類" className="flex gap-6 border-b border-border">
                {tabs.map(({ id, label }) => (
                    <button
                        key={id}
                        type="button"
                        role="tab"
                        id={`settings-tab-${id}`}
                        aria-selected={active === id}
                        aria-controls={`settings-panel-${id}`}
                        tabIndex={active === id ? 0 : -1}
                        onClick={() => setTab(id)}
                        className={cn(
                            "-mb-px border-b-2 px-1 pb-3 text-sm font-medium transition-colors",
                            active === id ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
                        )}
                    >
                        {label}
                    </button>
                ))}
            </div>
            )}
            {active === "general" ? (
                <div role="tabpanel" id="settings-panel-general" aria-labelledby={tabs.length > 1 ? "settings-tab-general" : undefined} aria-label={tabs.length > 1 ? undefined : "一般"} className="space-y-10">
                    <AppearanceSetting />
                    <SettingsPanel state={state} level="user" />
                </div>
            ) : (
                <div role="tabpanel" id="settings-panel-dev" aria-labelledby="settings-tab-dev">
                    <SettingsPanel state={state} level="dev" />
                </div>
            )}
        </PageContainer>
    );
}
