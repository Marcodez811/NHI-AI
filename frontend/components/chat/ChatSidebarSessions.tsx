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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useChatSessions } from "@/lib/hooks/useChatSessions"

const UNTITLED = "未命名對話"

function isMeaningfulTitle(title: string | null | undefined): title is string {
  const value = (title ?? "").trim()
  return value !== "" && value !== "新對話" && /[\p{L}\p{N}]/u.test(value)
}

function shortDate(iso: string | undefined): string {
  const date = iso ? new Date(iso) : null
  if (!date || Number.isNaN(date.getTime())) return ""
  const days = Math.floor((Date.now() - date.getTime()) / 86_400_000)
  if (days < 1) return "今天"
  if (days < 2) return "昨天"
  if (days < 7) return `${days} 天前`
  return `${date.getMonth() + 1}/${date.getDate()}`
}

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
    <div className="flex flex-col gap-px">
      {sessions.map((session) => {
        const href = `/chat/${session.id}`
        const current = pathname === href
        const named = isMeaningfulTitle(session.title)
        const label = named ? session.title.trim() : UNTITLED

        if (renamingId === session.id) {
          // Same row height as a normal item, so nothing jumps while renaming.
          return (
            <div key={session.id} className="flex h-8 items-center">
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
                className="h-8 min-w-0 flex-1 rounded-lg border border-ring bg-background px-2 text-sm text-foreground outline-none focus-visible:ring-0 focus-visible:ring-offset-0"
                aria-label="重新命名對話"
              />
            </div>
          )
        }

        return (
          <div
            key={session.id}
            className={cn(
              "group/session relative flex h-8 items-center rounded-lg transition-colors",
              current ? "bg-sidebar-accent" : "hover:bg-sidebar-accent/70 has-[[data-popup-open]]:bg-sidebar-accent/70",
            )}
          >
            <Link
              href={href}
              aria-current={current ? "page" : undefined}
              className="flex h-full min-w-0 flex-1 items-center rounded-lg pr-8 pl-2 text-sm text-sidebar-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring"
            >
              <span className={cn("min-w-0 flex-1 truncate", !named && "text-muted-foreground")}>{label}</span>
              {!named && <span className="ml-2 shrink-0 text-xs text-muted-foreground/70 group-hover/session:hidden">{shortDate(session.updated_at)}</span>}
            </Link>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`「${label}」的更多操作`}
                    className="absolute right-1 shrink-0 text-muted-foreground opacity-0 hover:bg-transparent hover:text-foreground focus-visible:opacity-100 group-hover/session:opacity-100 data-[popup-open]:opacity-100"
                  />
                }
              >
                <MoreHorizontal size={15} />
              </DropdownMenuTrigger>
              <DropdownMenuContent side="bottom" align="end">
                <DropdownMenuItem
                  onClick={() => {
                    setRenamingId(session.id)
                    setRenameValue(named ? session.title : "")
                  }}
                >
                  <Pencil size={14} />重新命名
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => setPendingDelete({ id: session.id, title: label })}
                >
                  <Trash2 size={14} />刪除
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
