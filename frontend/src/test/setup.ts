import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(window, "ResizeObserver", { writable: true, value: ResizeObserverMock });
Object.defineProperty(globalThis, "ResizeObserver", { writable: true, value: ResizeObserverMock });
Object.defineProperty(navigator, "clipboard", {
  configurable: true,
  value: { writeText: async () => undefined },
});
