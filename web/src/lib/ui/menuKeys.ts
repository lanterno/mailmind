// What a popover menu of numbered choices needs from the keyboard.
import { useEffect, useRef } from "react";

// A popover isn't placed yet as it mounts, so autoFocus finds it hidden and the dialog around it takes the focus. This moves
// focus in once it's on screen, so Tab starts inside. Whoever goes into one of its fields before that frame stays there: a field
// clicked into isn't left for the first one as typing starts, and a field left with Esc isn't gone back into. A button inside
// taking the focus doesn't count: as the guide steps on, the focus lands on one of the new card's before that frame.
export function useFocusFirst<T extends HTMLElement>(selector = "button") {
  const inside = useRef<T>(null);
  useEffect(() => {
    const frame = requestAnimationFrame(() => inside.current?.querySelector<HTMLElement>(selector)?.focus());
    const wentIn = (e: Event) => {
      const at = e.target as Element;
      if (inside.current?.contains(at) && at.matches?.("input, textarea, select")) cancelAnimationFrame(frame);
    };
    window.addEventListener("focusin", wentIn);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("focusin", wentIn);
    };
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
