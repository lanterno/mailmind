// What a popover menu of numbered choices needs from the keyboard.
import { useEffect, useRef } from "react";

// A popover isn't placed yet as it mounts, so autoFocus finds it hidden and the dialog around it takes the focus. This moves
// focus in once it's on screen, so Tab starts inside.
export function useFocusFirst<T extends HTMLElement>(selector = "button") {
  const inside = useRef<T>(null);
  useEffect(() => {
    const frame = requestAnimationFrame(() => inside.current?.querySelector<HTMLElement>(selector)?.focus());
    return () => cancelAnimationFrame(frame);
  }, [selector]);
  return inside;
}

// 1 to n picks a choice for as long as the menu is up, wherever in it the focus has got to, and from the moment it opens.
// A digit typed into the menu's own time field is the time.
export function useDigitKeys(count: number, pick: (index: number) => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const n = /^[1-9]$/.test(e.key) ? Number(e.key) : 0;
      if (!n || n > count || e.metaKey || e.ctrlKey || e.altKey || (e.target as Element | null)?.closest?.("input")) return;
      e.preventDefault();
      pick(n - 1);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });
}
