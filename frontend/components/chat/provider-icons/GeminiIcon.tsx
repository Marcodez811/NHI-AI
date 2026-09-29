import { useId } from "react";
import type { SVGProps } from "react";

/** Gemini keeps its brand gradient; the gradient id is unique per instance via useId. */
export function GeminiIcon(props: SVGProps<SVGSVGElement>) {
    const gradientId = `gemini-gradient-${useId().replace(/:/g, "")}`;
    return (
        <svg fill="none" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" {...props}>
            <path
                d="M16 8.016A8.522 8.522 0 008.016 16h-.032A8.521 8.521 0 000 8.016v-.032A8.521 8.521 0 007.984 0h.032A8.522 8.522 0 0016 7.984v.032z"
                fill={`url(#${gradientId})`}
            />
            <defs>
                <radialGradient
                    id={gradientId}
                    cx="0"
                    cy="0"
                    r="1"
                    gradientUnits="userSpaceOnUse"
                    gradientTransform="matrix(16.1326 5.4553 -43.70045 129.2322 1.588 6.503)"
                >
                    <stop offset=".067" stopColor="#9168C0" />
                    <stop offset=".343" stopColor="#5684D1" />
                    <stop offset=".672" stopColor="#1BA1E3" />
                </radialGradient>
            </defs>
        </svg>
    );
}
