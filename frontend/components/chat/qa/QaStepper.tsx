"use client";

import { Check } from "lucide-react";
import { QA_STAGES } from "../../../lib/api/qa";
import type { QaCardKind, QaStage } from "../../../lib/api/qa";

const STEPS: Array<{ stage: QaStage; label: string; card: QaCardKind }> = [
    { stage: "questions", label: "題目", card: "questions" },
    { stage: "documents", label: "資料", card: "documents" },
    { stage: "evidence", label: "整理", card: "evidence" },
    { stage: "outline", label: "架構", card: "outline" },
    { stage: "ready", label: "完成", card: "outline" },
];

/** Scrolls to the newest card of a kind; returns false when none is on screen. */
function scrollToLatestCard(kind: QaCardKind): boolean {
    const cards = document.querySelectorAll(`[data-qa-card="${kind}"]`);
    const target = cards[cards.length - 1];
    if (!target) return false;
    target.scrollIntoView?.({ behavior: "smooth", block: "start" });
    return true;
}

/** One slim line showing where the 立院QA workspace is; finished steps jump to their card. */
export function QaStepper({ stage }: { stage: QaStage }) {
    const current = QA_STAGES.indexOf(stage);
    return (
        <nav
            aria-label="立院QA 進度"
            data-slot="qa-stepper"
            className="sticky top-0 z-10 -mx-4 overflow-x-auto bg-background/95 px-4 py-2 backdrop-blur sm:mx-0 sm:px-0"
        >
            <ol className="flex w-max min-w-full items-center gap-1 text-xs">
                {STEPS.map((step, index) => {
                    const done = index < current || (stage === "ready" && index === current);
                    const active = index === current && !done;
                    const base = "inline-flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 font-medium";
                    const tone = active
                        ? "bg-primary text-primary-foreground"
                        : done
                            ? "bg-primary/10 text-primary"
                            : "text-muted-foreground";
                    return (
                        <li key={step.stage} className="flex items-center gap-1" aria-current={active ? "step" : undefined}>
                            {index > 0 && <span className="h-px w-3 bg-border" aria-hidden="true" />}
                            {done ? (
                                <button
                                    type="button"
                                    onClick={() => scrollToLatestCard(step.card)}
                                    className={`${base} ${tone} hover:bg-primary/20`}
                                    data-state="done"
                                >
                                    <Check size={12} aria-hidden="true" />
                                    {step.label}
                                </button>
                            ) : (
                                <span className={`${base} ${tone}`} data-state={active ? "current" : "todo"}>{step.label}</span>
                            )}
                        </li>
                    );
                })}
            </ol>
        </nav>
    );
}
