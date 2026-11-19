import type { ComponentProps, ReactNode } from "react"

import { cn } from "@/lib/utils"

/** Shared workspace page wrapper: identical padding on every tab, two widths. */
export function PageContainer({
  width = "narrow",
  className,
  children,
  ...props
}: { width?: "narrow" | "wide" } & ComponentProps<"section">) {
  // Both widths share the same frame and padding; narrow pages (forms,
  // settings) are centred inside it so they don't hug the left on wide screens.
  return (
    <section className="mx-auto w-full max-w-6xl px-5 py-8 lg:px-10" {...props}>
      <div className={cn(width === "narrow" && "mx-auto max-w-3xl", className)}>{children}</div>
    </section>
  )
}

/** Shared page title block: breadcrumb, title, description and right-aligned actions. */
export function PageHeader({
  title,
  description,
  actions,
  breadcrumb,
  icon,
  titleId,
  className,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  breadcrumb?: ReactNode
  icon?: ReactNode
  titleId?: string
  className?: string
}) {
  return (
    <header className={cn("mb-7 flex flex-wrap items-center justify-between gap-4", className)}>
      <div className="flex min-w-0 items-start gap-3">
        {icon}
        <div className="min-w-0">
          {breadcrumb && <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">{breadcrumb}</div>}
          <h1 id={titleId} className="text-2xl font-semibold tracking-tight">{title}</h1>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  )
}
