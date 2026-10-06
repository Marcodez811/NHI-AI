import { cn } from "@/lib/utils";
import { ClaudeIcon } from "./provider-icons/ClaudeIcon";
import { GeminiIcon } from "./provider-icons/GeminiIcon";
import { OpenAIIcon } from "./provider-icons/OpenAIIcon";

const PROVIDER_LABELS: Record<string, string> = {
    openai: "OpenAI",
    anthropic: "Anthropic",
    gemini: "Google Gemini",
};

export function providerLabel(provider: string): string {
    return PROVIDER_LABELS[provider] ?? provider;
}

/** Groups models by provider, keeping the catalog's order of first appearance. */
export function groupByProvider<T extends { provider: string }>(models: T[]): { provider: string; models: T[] }[] {
    const groups = new Map<string, T[]>();
    for (const model of models) {
        const list = groups.get(model.provider) ?? [];
        list.push(model);
        groups.set(model.provider, list);
    }
    return [...groups].map(([provider, items]) => ({ provider, models: items }));
}

const PROVIDER_ICONS: Record<string, typeof OpenAIIcon> = {
    openai: OpenAIIcon,
    anthropic: ClaudeIcon,
    gemini: GeminiIcon,
};

/**
 * Small brand mark for a chat model's provider, used in the model picker.
 *
 * Decorative: the model name next to it already identifies the choice, so the
 * mark is hidden from screen readers (otherwise an option reads "Anthropic
 * Claude Sonnet 5"). The provider name stays available as a hover title.
 */
export function ProviderIcon({ provider, className, disabled = false }: {
    provider: string;
    className?: string;
    disabled?: boolean;
}) {
    const label = PROVIDER_LABELS[provider] ?? provider;
    const Icon = PROVIDER_ICONS[provider];

    if (!Icon) {
        return (
            <span
                aria-hidden="true"
                data-provider-icon={provider}
                title={label}
                className={cn(
                    "inline-flex size-3.5 shrink-0 items-center justify-center rounded-[3px] bg-muted-foreground/20 text-[8px] font-semibold leading-none text-muted-foreground",
                    disabled && "opacity-40",
                    className,
                )}
            >
                {label.charAt(0).toUpperCase()}
            </span>
        );
    }

    return (
        <span aria-hidden="true" data-provider-icon={provider} title={label} className="inline-flex shrink-0">
            <Icon aria-hidden="true" className={cn("size-3.5 shrink-0", disabled && "opacity-40", className)} />
        </span>
    );
}
