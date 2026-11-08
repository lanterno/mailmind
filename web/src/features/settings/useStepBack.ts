import { useEffect, useEffectEvent } from "react";

// Esc steps back one thing at a time: out of a field, out of whatever `step` closes (a form), and only then out of Settings.
export function useStepBack(step?: () => boolean) {
  const stepBack = useEffectEvent((e: KeyboardEvent) => {
    if (e.key !== "Escape") return;
    const at = e.target as HTMLElement;
    if (at.closest?.("input,textarea,select")) at.blur();
    else if (!step?.()) return;
    e.stopPropagation();
    e.preventDefault();
  });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => stepBack(e);
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);
}
