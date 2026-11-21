import "@testing-library/jest-dom/vitest";

// Mantine reads these browser APIs that jsdom does not provide.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = window.ResizeObserver ?? (ResizeObserverStub as unknown as typeof ResizeObserver);
// Combobox dropdowns (Select) scroll the selected option into view.
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});

// The rule and mapping editors are large forms; under a parallel CI run 1 s for findBy* is too tight.
import { configure } from "@testing-library/react";
configure({ asyncUtilTimeout: 5000 });
