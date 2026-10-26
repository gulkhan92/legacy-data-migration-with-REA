import { useEffect, useSyncExternalStore } from "react";

/**
 * Pages are loaded on demand. While one is loading the layout shows a placeholder, and when the page arrives it is usually taller
 * or shorter, so everything below it (the footer) would jump. The footer is hidden while any placeholder is on screen, so that
 * nothing visible moves; it appears, without moving, once the page is there.
 */
let count = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function usePageLoading(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => count > 0,
    () => false,
  );
}

/** Rendered by a page placeholder: while it is mounted the page counts as loading. */
export function PageLoadingMarker() {
  useEffect(() => {
    count += 1;
    emit();
    return () => {
      count -= 1;
      emit();
    };
  }, []);
  return null;
}
