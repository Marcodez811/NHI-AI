"use client"

import { ChevronRight, Library, Plus, Settings, Workflow } from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useEffect, useState } from "react"

import { cn } from "@/lib/utils"
import { NhiBrand } from "./nhi-brand"
import { ThemeMenu } from "./theme-menu"
import { ChatSidebarSessions } from "@/components/chat/ChatSidebarSessions"

const navigation = [
  { href: "/chat", label: "新對話", icon: Plus },
  { href: "/knowledge", label: "知識庫管理", icon: Library },
  { href: "/workflows", label: "AI 工作流", icon: Workflow },
  { href: "/settings", label: "設定", icon: Settings },
] as const

function isCurrentPath(pathname: string, href: string): boolean {
  if (href === "/workflows" && (pathname === "/slides" || pathname.startsWith("/slides/"))) return true
  // "新對話" only highlights on the fresh /chat composer, not on a saved
  // conversation route (/chat/[id]) — that highlight belongs to the
  // matching row in the recent-conversations list instead.
  if (href === "/chat") return pathname === "/chat"
  return pathname === href || pathname.startsWith(`${href}/`)
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? ""
  const [collapsed, setCollapsed] = useState(false)
  const [preferenceReady, setPreferenceReady] = useState(false)

  useEffect(() => {
    try {
      setCollapsed(window.localStorage.getItem("nhi-sidebar-collapsed") === "true")
    } catch {
      // Storage can be unavailable in restricted or private browser contexts.
    } finally {
      setPreferenceReady(true)
    }
  }, [])

  useEffect(() => {
    if (!preferenceReady) return
    try {
      window.localStorage.setItem("nhi-sidebar-collapsed", String(collapsed))
    } catch {
      // The sidebar remains usable even when the preference cannot persist.
    }
  }, [collapsed, preferenceReady])

  return (
    <div className="min-h-screen bg-background text-foreground">
      <aside className={cn("fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-sidebar-border bg-sidebar transition-[width] md:flex", collapsed ? "w-24" : "w-60")}>
        <div className={cn("relative flex h-16 shrink-0 items-center", collapsed ? "justify-center px-3" : "gap-3 px-5")}>
          <Link href="/chat" aria-label="健保署 AI" className="min-w-0 text-sidebar-foreground">
            <NhiBrand compact={collapsed} />
          </Link>
          <button type="button" aria-label={collapsed ? "展開側邊欄" : "收合側邊欄"} onClick={() => setCollapsed((value) => !value)} className={cn("rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground", collapsed ? "absolute right-2 size-7" : "ml-auto")}>
            <ChevronRight className={cn("size-4 transition-transform", collapsed ? "" : "rotate-180")} />
          </button>
        </div>
        <nav className="flex shrink-0 flex-col gap-1 px-3 pt-2 pb-1" aria-label="主要導覽">
          {navigation.map(({ href, label, icon: Icon }) => {
            const current = isCurrentPath(pathname, href)
            return (
              <Link key={href} href={href} aria-label={collapsed ? label : undefined} title={collapsed ? label : undefined} aria-current={current ? "page" : undefined} className={cn(
                "flex h-10 items-center gap-3 rounded-lg text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                collapsed ? "justify-center px-0" : "px-3",
                current ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
              )}>
                <Icon className="size-4" aria-hidden="true" />
                {!collapsed && label}
              </Link>
            )
          })}
        </nav>
        {!collapsed && (
          <div className="flex min-h-0 flex-1 flex-col px-3 pb-2">
            <div className="px-3 pt-3 pb-1 text-xs font-medium text-muted-foreground">最近對話</div>
            <div className="flex-1 overflow-y-auto">
              <ChatSidebarSessions />
            </div>
          </div>
        )}
        {collapsed && <div className="flex-1" />}
        <div className="mt-auto shrink-0 border-t border-sidebar-border p-3">
          <div className={cn("flex h-10 items-center gap-3 rounded-md text-sm text-muted-foreground", collapsed ? "justify-center" : "px-3")}>
            <ThemeMenu />
            {!collapsed && <span>外觀</span>}
          </div>
        </div>
      </aside>
      <header className="sticky top-0 z-20 flex h-14 items-center border-b border-border bg-background/95 px-4 backdrop-blur md:hidden">
        <Link href="/chat" aria-label="健保署 AI" className="text-foreground"><NhiBrand /></Link>
        <div className="ml-auto"><ThemeMenu /></div>
      </header>
      <main className={cn("min-h-screen pb-20 transition-[margin] md:pb-0", collapsed ? "md:ml-24" : "md:ml-60")}>{children}</main>

      <nav
        className="fixed inset-x-3 bottom-3 z-30 grid grid-cols-4 gap-1 rounded-xl border border-border bg-card/95 p-1.5 shadow-lg backdrop-blur md:hidden"
        aria-label="主要導覽"
      >
        {navigation.map(({ href, label, icon: Icon }) => {
          const current = isCurrentPath(pathname, href)
          return (
            <Link
              key={href}
              href={href}
              aria-current={current ? "page" : undefined}
              className={cn(
                "flex min-h-11 flex-col items-center justify-center gap-0.5 rounded-lg text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                current
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              <Icon className="size-4" aria-hidden="true" />
              {label === "知識庫管理" ? "知識庫" : label}
            </Link>
          )
        })}
      </nav>
    </div>
  )
}
