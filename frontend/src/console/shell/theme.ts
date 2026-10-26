import { useCallback, useEffect, useSyncExternalStore } from "react";

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const KEY = "legacylift.theme";
const listeners = new Set<() => void>();

function read(): ThemeChoice {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" || value === "system" ? value : "system";
  } catch {
    return "system";
  }
}

let choice: ThemeChoice = typeof window === "undefined" ? "system" : read();

const prefersDark = (): boolean => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;

export function resolve(value: ThemeChoice, systemDark = prefersDark()): ResolvedTheme {
  return value === "system" ? (systemDark ? "dark" : "light") : value;
}

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Puts the theme on the document root, where portals (dialogs, menus) inherit it as well. */
export function applyTheme(value: ThemeChoice): void {
  const dark = resolve(value) === "dark";
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}

export function clearTheme(): void {
  document.documentElement.classList.remove("dark");
  document.documentElement.style.colorScheme = "";
}

/** The theme of the console. The public site is always light: the console applies its theme when it opens and removes it when it closes. */
export function useTheme(): { choice: ThemeChoice; resolved: ResolvedTheme; setChoice: (value: ThemeChoice) => void; toggle: () => void } {
  const current = useSyncExternalStore(
    subscribe,
    () => choice,
    () => "system" as ThemeChoice,
  );
  const resolved = resolve(current);
  const setChoice = useCallback((value: ThemeChoice) => {
    choice = value;
    try {
      window.localStorage.setItem(KEY, value);
    } catch {
      /* the choice then lasts until the page closes */
    }
    applyTheme(value);
    emit();
  }, []);
  const toggle = useCallback(() => setChoice(resolve(choice) === "dark" ? "light" : "dark"), [setChoice]);
  return { choice: current, resolved, setChoice, toggle };
}

/** Applies the theme while the console is on screen, and follows the system setting when the choice is "system". */
export function useConsoleTheme(): void {
  useEffect(() => {
    applyTheme(choice);
    const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    const onChange = () => {
      if (choice === "system") {
        applyTheme(choice);
        emit();
      }
    };
    media?.addEventListener("change", onChange);
    return () => {
      media?.removeEventListener("change", onChange);
      clearTheme();
    };
  }, []);
}
