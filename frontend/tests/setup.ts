import "@testing-library/jest-dom/vitest";

// jsdom has no PointerEvent. The menu components (@base-ui/react) construct
// one while handling clicks; without it they throw uncaught errors, which
// make the whole test run exit non-zero even when every test passes.
if (typeof window !== "undefined" && typeof window.PointerEvent === "undefined") {
    class PointerEventPolyfill extends MouseEvent {
        readonly pointerId: number;
        readonly pointerType: string;
        readonly isPrimary: boolean;

        constructor(type: string, init: PointerEventInit = {}) {
            super(type, init);
            this.pointerId = init.pointerId ?? 1;
            this.pointerType = init.pointerType ?? "mouse";
            this.isPrimary = init.isPrimary ?? true;
        }
    }
    window.PointerEvent = PointerEventPolyfill as unknown as typeof PointerEvent;
}
