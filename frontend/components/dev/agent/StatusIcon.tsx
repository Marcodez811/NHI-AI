import { CheckCircle2, Circle, LoaderCircle, XCircle } from "lucide-react";

export function StatusIcon({ status }: { status: string | null | undefined }) {
    if (status === "completed") return <CheckCircle2 size={15} aria-hidden="true" />;
    if (status === "failed") return <XCircle size={15} aria-hidden="true" />;
    if (status === "running") return <LoaderCircle size={15} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />;
    return <Circle size={15} aria-hidden="true" />;
}
