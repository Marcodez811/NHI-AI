"use client";

import { Laptop, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

const THEMES = [
    { value: "light", label: "淺色", icon: Sun },
    { value: "dark", label: "深色", icon: Moon },
    { value: "system", label: "跟隨系統", icon: Laptop },
] as const;

/** Per-browser display mode (next-themes keeps it in localStorage). */
export function AppearanceSetting() {
    const { theme, setTheme } = useTheme();
    const [mounted, setMounted] = useState(false);
    useEffect(() => setMounted(true), []);
    const current = mounted ? (theme ?? "system") : null;

    return (
        <section aria-labelledby="appearance-heading" className="space-y-3">
            <div className="space-y-1">
                <h2 id="appearance-heading" className="text-base font-semibold">外觀</h2>
                <p className="text-sm text-muted-foreground">選擇此瀏覽器的顯示模式。</p>
            </div>
            <div role="radiogroup" aria-labelledby="appearance-heading" className="inline-flex rounded-lg border border-border bg-muted p-1">
                {THEMES.map(({ value, label, icon: Icon }) => (
                    <button
                        key={value}
                        type="button"
                        role="radio"
                        aria-checked={current === value}
                        onClick={() => setTheme(value)}
                        className={cn(
                            "inline-flex h-8 items-center gap-2 rounded-md px-3 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            current === value ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                        )}
                    >
                        <Icon className="size-4" aria-hidden="true" />
                        {label}
                    </button>
                ))}
            </div>
        </section>
    );
}
