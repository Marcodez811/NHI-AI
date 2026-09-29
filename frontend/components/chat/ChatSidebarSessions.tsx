"use client"

import { useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { MoreHorizontal, Pencil, Trash2 } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useChatSessions } from "@/lib/hooks/useChatSessions"

/** The recent-conversation list nested under the sidebar's 「對話」 entry. */
export function ChatSidebarSessions() {
  const pathname = usePathname() ?? ""
  const { sessions, rename, remove } = useChatSessions()
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState("")

  if (!sessions.length) return null

  const commitRename = async (id: string) => {
    const title = renameValue.trim()
    setRenamingId(null)
    if (title) await rename(id, title).catch(() => undefined)
  }

  return (
    <div className="mt-1 flex flex-col gap-0.5">
      {sessions.map((session) => {
        const href = `/chat/${session.id}`
        const current = pathname === href

        if (renamingId === session.id) {
          return (
            <input
              key={session.id}
              autoFocus
              value={renameValue}
              onChange={(event) => setRenameValue(event.target.value)}
              onBlur={() => void commitRename(session.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void commitRename(session.id)
                if (event.key === "Escape") setRenamingId(null)
              }}
              className="h-8 rounded-md border border-input bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="重新命名對話"
            />
          )
        }

        return (
          <div key={session.id} className="group/session flex items-center gap-1">
            <Link
              href={href}
              aria-current={current ? "page" : undefined}
              className={cn(
                "flex h-8 min-w-0 flex-1 items-center rounded-md px-3 text-sm transition-colors",
                current
                  ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
              )}
            >
              <span className="truncate">{session.title}</span>
            </Link>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`「${session.title}」的更多操作`}
                    className="shrink-0 opacity-0 focus-visible:opacity-100 group-hover/session:opacity-100 data-[popup-open]:opacity-100"
                  />
                }
              >
                <MoreHorizontal size={13} />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-36">
                <DropdownMenuItem
                  onClick={() => {
                    setRenamingId(session.id)
                    setRenameValue(session.title)
                  }}
                >
                  <Pencil size={13} />重新命名
                </DropdownMenuItem>
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => {
                    if (window.confirm(`刪除對話「${session.title}」？此操作無法復原。`)) void remove(session.id)
                  }}
                >
                  <Trash2 size={13} />刪除
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      })}
    </div>
  )
}
