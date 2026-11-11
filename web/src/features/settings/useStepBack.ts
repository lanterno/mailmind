import { useEffect, useEffectEvent } from "react";

// Out of a field and onto the dialog around it, which keeps the focus inside: a blur leaves it nowhere, and the dialog's
// focus trap puts it straight back in the field.
function leave(field: HTMLElement) {
  const dialog = field.closest<HTMLElement>('[role="dialog"]');
  if (dialog) dialog.focus();
  else field.blur();
}

// Esc steps back one thing at a time: out of a field, out of whatever `step` closes (a form), and only then out of Settings.
export function useStepBack(step?: () => boolean) {
  const stepBack = useEffectEvent((e: KeyboardEvent) => {
    if (e.key !== "Escape") return;
    const at = e.target as HTMLElement;
    if (at.closest?.("input,textarea,select")) leave(at);
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
