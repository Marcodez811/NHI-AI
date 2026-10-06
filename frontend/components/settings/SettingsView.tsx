"use client";

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
        <div className="mx-auto w-full max-w-3xl space-y-8 px-4 py-8 sm:px-6 lg:px-8">
            <header className="space-y-2">
                <h1 className="text-2xl font-semibold tracking-tight">設定</h1>
            </header>
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
            {active === "general" ? (
                <div role="tabpanel" id="settings-panel-general" aria-labelledby="settings-tab-general" className="space-y-8">
                    <AppearanceSetting />
                    <SettingsPanel state={state} level="user" />
                </div>
            ) : (
                <div role="tabpanel" id="settings-panel-dev" aria-labelledby="settings-tab-dev">
                    <SettingsPanel state={state} level="dev" />
                </div>
            )}
        </div>
    );
}
