"use client";

import { ChevronRight, Library, MessageCircle, Presentation } from "lucide-react";
import { Button } from "../ui/button";
import type { View } from "../../lib/workspace/types";

export function Sidebar({
    view,
    setView,
    collapsed,
    setCollapsed,
    documentCount,
    hasError,
}: {
    view: View;
    setView: (view: View) => void;
    collapsed: boolean;
    setCollapsed: (value: boolean) => void;
    documentCount: number;
    hasError: boolean;
}) {
    const navItems = [
        ["chat", "對話", MessageCircle],
        ["files", "知識庫", Library],
        ["slides", "簡報生成", Presentation],
    ] as const;
    return (
        <>
            <aside
                className={`fixed inset-y-0 left-0 z-20 hidden border-r border-border bg-sidebar transition-all md:flex md:flex-col ${collapsed ? "w-20" : "w-64"}`}
            >
                <div
                    className={`flex h-16 items-center border-b border-border ${collapsed ? "justify-center px-3" : "gap-3 px-5"}`}
                >
                    <div className={collapsed ? "hidden" : ""}>
                        <div className="text-lg font-semibold tracking-tight">
                            健保署 AI
                        </div>
                        <div className="text-[10px] uppercase tracking-[.18em] text-muted-foreground">
                            Powered By FlySheet
                        </div>
                    </div>
                    <Button
                        type="button"
                        onClick={() => setCollapsed(!collapsed)}
                        aria-label="切換側邊欄"
                        variant="ghost"
                        size="icon"
                        className={`${collapsed ? "" : "ml-auto"} text-muted-foreground`}
                    >
                        <ChevronRight
                            size={17}
                            className={collapsed ? "" : "rotate-180"}
                        />
                    </Button>
                </div>
                <nav className="flex flex-col gap-1 p-3" aria-label="主要導覽">
                    {navItems.map(([id, label, Icon]) => (
                        <Button
                            type="button"
                            key={id}
                            onClick={() => setView(id)}
                            aria-label={label}
                            aria-current={view === id ? "page" : undefined}
                            variant={view === id ? "default" : "ghost"}
                            className={`h-auto w-full justify-start py-2.5 text-sm font-bold ${collapsed ? "justify-center px-2" : "gap-3 px-3"}`}
                        >
                            <Icon size={17} aria-hidden="true" />
                            {!collapsed && label}
                        </Button>
                    ))}
                </nav>
                {!collapsed && (
                    <div className="mt-auto border-t border-border p-4 text-sm">
                        {documentCount} 份文件{" "}
                        <span
                            className={`float-right mt-1 size-2 rounded-full ${hasError ? "bg-amber-500" : "bg-emerald-500"}`}
                            aria-label={
                                hasError ? "有待處理的錯誤" : "系統正常"
                            }
                        />
                    </div>
                )}
            </aside>
            <nav
                className="fixed inset-x-3 bottom-3 z-30 grid grid-cols-3 gap-1 rounded-2xl border border-border bg-card p-1.5 shadow-lg md:hidden"
                aria-label="主要導覽"
            >
                {navItems.map(([id, label, Icon]) => (
                    <Button
                        type="button"
                        key={id}
                        onClick={() => setView(id)}
                        aria-label={label}
                        aria-current={view === id ? "page" : undefined}
                        variant={view === id ? "default" : "ghost"}
                        className="h-auto min-h-12 flex-col gap-1 rounded-xl px-2 text-[11px] font-semibold"
                    >
                        <Icon size={17} aria-hidden="true" />
                        <span>{label}</span>
                    </Button>
                ))}
            </nav>
        </>
    );
}
