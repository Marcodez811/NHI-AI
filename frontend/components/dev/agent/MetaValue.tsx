import { Copy } from "lucide-react";
import { Button } from "../../ui/button";
import { copyValue } from "./format";

export function MetaValue({ label, value, copyable = false }: { label: string; value: string; copyable?: boolean }) {
    return (
        <div className="min-w-0">
            <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
            <dd className="mt-1 flex min-w-0 items-center gap-1 text-sm">
                <span className="min-w-0 truncate font-mono">{value}</span>
                {copyable && value !== "—" && (
                    <Button
                        type="button"
                        onClick={() => copyValue(value)}
                        variant="ghost"
                        size="icon-xs"
                        className="shrink-0"
                        aria-label={`複製${label}`}
                        title={`複製${label}`}
                    >
                        <Copy size={12} aria-hidden="true" />
                    </Button>
                )}
            </dd>
        </div>
    );
}
