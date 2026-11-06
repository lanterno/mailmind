// On a touch screen, a row swiped left archives and swiped right snoozes to tomorrow. Both undo with z. Vertical movement
// stays the list's: the gesture is only ours once it's clearly sideways.
import { useEffect, useRef } from "react";

const THRESHOLD = 80;

export function useSwipe(archive: (pk: number) => void, snooze: (pk: number) => void) {
  const verbs = useRef({ archive, snooze });
  useEffect(() => {
    verbs.current = { archive, snooze };
  });
  useEffect(() => {
    const list = document.getElementById("list"), badge = document.getElementById("swipeBadge");
    if (!list || !badge) return;
    let at: { row: HTMLElement; pk: number; x0: number; y0: number; dx: number; lock: boolean } | null = null;
    const start = (e: TouchEvent) => {
      if (e.touches.length !== 1) return;
      const target = e.target as Element, row = target.closest<HTMLElement>("#rows .row.cmsg");
      if (!row || target.closest(".cbx")) return;     // the select box stays what it is
      const t = e.touches[0]!;
      at = { row, pk: Number(row.dataset["pk"]), x0: t.clientX, y0: t.clientY, dx: 0, lock: false };
    };
    const move = (e: TouchEvent) => {
      if (!at) return;
      const t = e.touches[0]!, dx = t.clientX - at.x0, dy = t.clientY - at.y0;
      if (!at.lock) {
        if (Math.abs(dx) < 10 && Math.abs(dy) < 10) return;
        if (Math.abs(dy) >= Math.abs(dx)) {
          at = null;
          return;
        }
        at.lock = true;
        at.row.style.transition = "none";
      }
      e.preventDefault();
      at.dx = dx;
      at.row.style.transform = `translateX(${dx}px)`;
      if (Math.abs(dx) <= 14) {
        badge.className = "";
        return;
      }
      const left = dx < 0, r = at.row.getBoundingClientRect();
      badge.className = left ? "show archive" : "show snooze";
      badge.style.top = `${r.top + r.height / 2 - 17}px`;
      badge.style.left = left ? "auto" : "14px";
      badge.style.right = left ? "14px" : "auto";
      badge.style.opacity = Math.min(1, (Math.abs(dx) - 14) / 70).toFixed(2);
    };
    const end = () => {
      if (!at) return;
      const { row, pk, dx, lock } = at;
      at = null;
      badge.className = "";
      if (!lock) return;
      if (Math.abs(dx) > THRESHOLD) {
        row.style.transition = "";
        row.style.transform = "";
        if (dx < 0) verbs.current.archive(pk);
        else verbs.current.snooze(pk);
      } else {
        row.style.transition = "transform .2s var(--ease,ease)";
        row.style.transform = "";
      }
    };
    list.addEventListener("touchstart", start, { passive: true });
    list.addEventListener("touchmove", move, { passive: false });
    list.addEventListener("touchend", end);
    list.addEventListener("touchcancel", end);
    return () => {
      list.removeEventListener("touchstart", start);
      list.removeEventListener("touchmove", move);
      list.removeEventListener("touchend", end);
      list.removeEventListener("touchcancel", end);
    };
  }, []);
}
