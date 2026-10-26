import { useEffect, useRef, useState, type RefObject } from "react";

export const prefersReducedMotion = (): boolean => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Shows `target`; the first time the element scrolls into view it counts up from 0 to it. With reduced motion, or without IntersectionObserver, the final value is simply shown. */
export function useCountUp<T extends HTMLElement>(target: number | null, duration = 1200): { ref: RefObject<T>; value: number } {
  const ref = useRef<T>(null);
  // null means "not animating": the true value is shown, so a page that is never scrolled (print, a crawler) never shows a made up zero.
  const [animated, setAnimated] = useState<number | null>(null);
  const instant = target !== null && (prefersReducedMotion() || typeof IntersectionObserver === "undefined");

  useEffect(() => {
    if (target === null || instant) return;
    const element = ref.current;
    if (!element) return;
    let frame = 0;
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        observer.disconnect();
        const started = performance.now();
        const tick = (now: number) => {
          const t = Math.min(1, (now - started) / duration);
          setAnimated(target * (1 - Math.pow(1 - t, 3)));
          if (t < 1) frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
      },
      { threshold: 0.4 },
    );
    observer.observe(element);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [target, duration, instant]);

  return { ref, value: instant || animated === null ? (target ?? 0) : animated };
}
