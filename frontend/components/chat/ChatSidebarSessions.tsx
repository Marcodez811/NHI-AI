"use client"

import { useState } from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { MoreHorizontal, Pencil, Trash2 } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { ConfirmDialog } from "@/components/ui/confirm-dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useChatSessions } from "@/lib/hooks/useChatSessions"

/** The recent-conversation list shown under the sidebar's 「最近對話」 section header. */
export function ChatSidebarSessions() {
  const pathname = usePathname() ?? ""
  const { sessions, rename, remove } = useChatSessions()
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState("")
  const [pendingDelete, setPendingDelete] = useState<{ id: string; title: string } | null>(null)

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
          // Same row geometry as a normal item: the input takes the link's
          // width and a spacer holds the ⋯ button's place, so nothing jumps.
          return (
            <div key={session.id} className="flex items-center gap-1">
              <input
                autoFocus
                onFocus={(event) => event.currentTarget.select()}
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                onBlur={() => void commitRename(session.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void commitRename(session.id)
                  if (event.key === "Escape") setRenamingId(null)
                }}
                className="h-8 min-w-0 flex-1 rounded-lg border border-ring bg-background px-3 text-sm text-foreground outline-none focus-visible:ring-0 focus-visible:ring-offset-0"
                aria-label="重新命名對話"
              />
              <span className="size-6 shrink-0" aria-hidden="true" />
            </div>
          )
        }

        return (
          <div key={session.id} className="group/session flex items-center gap-1">
            <Link
              href={href}
              aria-current={current ? "page" : undefined}
              className={cn(
                "flex h-8 min-w-0 flex-1 items-center rounded-lg px-3 text-sm transition-colors",
                current
                  ? "bg-accent text-foreground"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
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
                  onClick={() => setPendingDelete({ id: session.id, title: session.title })}
                >
                  <Trash2 size={13} />刪除
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      })}
      <ConfirmDialog
        open={pendingDelete !== null}
        title={`刪除對話「${pendingDelete?.title ?? ""}」？`}
        description="此操作無法復原。"
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete) void remove(pendingDelete.id)
          setPendingDelete(null)
        }}
      />
    </div>
  )
}
