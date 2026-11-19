import Image from "next/image"

import { cn } from "@/lib/utils"

export function NhiBrand({ compact = false }: { compact?: boolean }) {
  return (
    <span className={cn("flex min-w-0 items-center gap-2.5", compact && "justify-center")}>
      <Image
        src="/nhi-logo-small.png"
        alt=""
        width={32}
        height={32}
        className="size-7 shrink-0 object-contain"
        priority
      />
      {!compact && <span className="truncate text-[17px] font-semibold tracking-tight">健保署 AI</span>}
    </span>
  )
}
