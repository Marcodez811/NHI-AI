import type { ReactNode } from "react";
import { Button } from "../ui/button";

import type {
    DocumentRead,
    DocumentStatus,
} from "../../lib/api/documents";

export function documentDisplayName(document: DocumentRead): string {
    return document.display_name || document.original_filename || document.id;
}

export function formatBytes(value?: number | null): string {
    if (!value) return "—";
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDate(value?: string | null): string {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.valueOf())
        ? value
        : new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium" }).format(
              date,
          );
}

export function formatDateTime(value?: string | null): string {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.valueOf())
        ? value
        : new Intl.DateTimeFormat("zh-TW", {
              dateStyle: "medium",
              timeStyle: "short",
          }).format(date);
}

export function statusLabel(status: DocumentStatus): string {
    return {
        queued: "排隊中",
        indexing: "索引中",
        ready: "就緒",
        failed: "失敗",
        deleting: "刪除中",
        delete_failed: "刪除失敗",
    }[status];
}

export function extensionOf(document: DocumentRead): string {
    const extension = document.extension?.toLowerCase();
    if (extension)
        return extension.startsWith(".") ? extension : `.${extension}`;
    const name = document.original_filename.toLowerCase();
    const dot = name.lastIndexOf(".");
    return dot >= 0 ? name.slice(dot) : "";
}

export function ActionButton({
    label,
    onClick,
    children,
    disabled = false,
    destructive = false,
}: {
    label: string;
    onClick: () => void;
    children: ReactNode;
    disabled?: boolean;
    destructive?: boolean;
}) {
    return (
        <Button
            type="button"
            onClick={onClick}
            disabled={disabled}
            aria-label={label}
            title={label}
            variant="ghost"
            size="icon-sm"
            className={`text-muted-foreground ${destructive ? "hover:text-red-600" : ""}`}
        >
            {children}
        </Button>
    );
}
