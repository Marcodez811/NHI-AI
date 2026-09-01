"use client"

import { Library, MessageCircle, Presentation } from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"

import { cn } from "@/lib/utils"
import { ThemeMenu } from "./theme-menu"

const navigation = [
  { href: "/chat", label: "對話", icon: MessageCircle },
  { href: "/knowledge", label: "知識庫", icon: Library },
  { href: "/slides", label: "簡報", icon: Presentation },
] as const

function isCurrentPath(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`)
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? ""

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
        <div className="mx-auto flex h-14 max-w-[1440px] items-center gap-3 px-4 sm:px-6 lg:px-8">
          <Link
            href="/chat"
            className="shrink-0 text-sm font-semibold tracking-tight text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            健保署 AI
          </Link>

          <nav className="hidden items-center gap-1 md:flex" aria-label="主要導覽">
            {navigation.map(({ href, label, icon: Icon }) => {
              const current = isCurrentPath(pathname, href)
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={current ? "page" : undefined}
                  className={cn(
                    "inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    current
                      ? "bg-secondary text-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground",
                  )}
                >
                  <Icon className="size-4" aria-hidden="true" />
                  {label}
                </Link>
              )
            })}
          </nav>

          <div className="ml-auto">
            <ThemeMenu />
          </div>
        </div>
      </header>

      <main className="min-h-[calc(100vh-3.5rem)] pb-20 md:pb-0">{children}</main>

      <nav
        className="fixed inset-x-3 bottom-3 z-30 grid grid-cols-3 gap-1 rounded-xl border border-border bg-card/95 p-1.5 shadow-lg backdrop-blur md:hidden"
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
              {label}
            </Link>
          )
        })}
      </nav>
    </div>
  )
}
