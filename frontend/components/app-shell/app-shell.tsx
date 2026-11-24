"use client"

import { ChevronDown, FolderOpen, Library, PanelLeftClose, PanelLeftOpen, Plus, Settings, Workflow } from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useEffect, useState } from "react"

import { cn } from "@/lib/utils"
import { NhiBrand } from "./nhi-brand"
import { ChatSidebarSessions } from "@/components/chat/ChatSidebarSessions"

const navigation = [
  { href: "/chat", label: "新對話", icon: Plus },
  { href: "/knowledge", label: "知識庫管理", icon: Library },
  { href: "/files", label: "我的檔案", icon: FolderOpen },
  { href: "/workflows", label: "AI 工作流", icon: Workflow },
] as const

const sidebarRow =
  "group/row flex items-center gap-2.5 rounded-lg text-sm text-sidebar-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
const sidebarIconButton =
  "inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"

function isCurrentPath(pathname: string, href: string): boolean {
  if (href === "/workflows" && ["/slides", "/legislative-qa"].some((base) => pathname === base || pathname.startsWith(`${base}/`))) return true
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
  const [recentsOpen, setRecentsOpen] = useState(true)

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
      <aside className={cn("fixed inset-y-0 left-0 z-30 hidden flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 md:flex", collapsed ? "w-14" : "w-64")}>
        <div className={cn("flex h-14 shrink-0 items-center", collapsed ? "justify-center" : "gap-2 pr-2 pl-4")}>
          {!collapsed && (
            <Link href="/chat" aria-label="健保署 AI" className="min-w-0 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <NhiBrand />
            </Link>
          )}
          <button
            type="button"
            aria-label={collapsed ? "展開側邊欄" : "收合側邊欄"}
            title={collapsed ? "展開側邊欄" : "收合側邊欄"}
            onClick={() => setCollapsed((value) => !value)}
            className={cn(sidebarIconButton, !collapsed && "ml-auto")}
          >
            {collapsed ? <PanelLeftOpen className="size-[18px]" /> : <PanelLeftClose className="size-[18px]" />}
          </button>
        </div>
        <nav className={cn("flex shrink-0 flex-col gap-0.5 pt-1 pb-2", collapsed ? "items-center px-2" : "px-2")} aria-label="主要導覽">
          {navigation.map(({ href, label, icon: Icon }) => {
            const current = isCurrentPath(pathname, href)
            return (
              <Link key={href} href={href} aria-label={collapsed ? label : undefined} title={collapsed ? label : undefined} aria-current={current ? "page" : undefined} className={cn(
                sidebarRow,
                collapsed ? "size-9 justify-center px-0" : "h-9 px-2",
                current ? "bg-sidebar-accent font-medium" : "hover:bg-sidebar-accent/70",
              )}>
                <span className="flex size-6 shrink-0 items-center justify-center">
                  <Icon className="size-[18px]" aria-hidden="true" />
                </span>
                {!collapsed && <span className="truncate">{label}</span>}
              </Link>
            )
          })}
        </nav>
        {!collapsed ? (
          <div className="flex min-h-0 flex-1 flex-col px-2">
            <button
              type="button"
              aria-expanded={recentsOpen}
              onClick={() => setRecentsOpen((value) => !value)}
              className="group/recents flex h-7 w-fit items-center gap-1 rounded-md px-2 text-xs font-medium text-muted-foreground hover:text-foreground"
            >
              最近對話
              <ChevronDown className={cn("size-3 opacity-0 transition group-hover/recents:opacity-100", !recentsOpen && "-rotate-90 opacity-100")} aria-hidden="true" />
            </button>
            <div className={cn("-mx-2 flex-1 overflow-y-auto px-2 pb-2", !recentsOpen && "hidden")}>
              <ChatSidebarSessions />
            </div>
          </div>
        ) : <div className="flex-1" />}
        <div className={cn("shrink-0 p-2", collapsed && "flex justify-center")}>
          <Link
            href="/settings"
            aria-label="設定"
            title={collapsed ? "設定" : undefined}
            aria-current={isCurrentPath(pathname, "/settings") ? "page" : undefined}
            className={cn(
              sidebarRow,
              collapsed ? "size-9 justify-center px-0" : "h-11 px-2",
              isCurrentPath(pathname, "/settings") ? "bg-sidebar-accent" : "hover:bg-sidebar-accent/70",
            )}
          >
            <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-sidebar-accent text-sidebar-accent-foreground ring-1 ring-sidebar-border">
              <Settings className="size-4" aria-hidden="true" />
            </span>
            {!collapsed && (
              <span className="flex min-w-0 flex-1 flex-col leading-tight">
                <span className="truncate font-medium">設定</span>
                <span className="truncate text-xs text-muted-foreground">模型、外觀</span>
              </span>
            )}
          </Link>
        </div>
      </aside>
      <header className="sticky top-0 z-20 flex h-14 items-center border-b border-border bg-background/95 px-4 backdrop-blur md:hidden">
        <Link href="/chat" aria-label="健保署 AI" className="text-foreground"><NhiBrand /></Link>
        <Link
          href="/settings"
          aria-label="設定"
          className="ml-auto inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Settings className="size-4" aria-hidden="true" />
        </Link>
      </header>
      <main className={cn("min-h-screen pb-20 transition-[margin] md:pb-0", collapsed ? "md:ml-14" : "md:ml-64")}>{children}</main>

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
