// The list's width: drag it, or focus it and use the arrows, the keyboard's way to the same thing. Kept under the key the
// current page uses.
import { useEffect, useState } from "react";
import { store, stored } from "../lib/store";

const MIN = 300, MAX = 560, START = 372, STEP = 24;
const clamp = (w: number) => Math.max(MIN, Math.min(MAX, Math.round(w)));

function initial() {
  const saved = Number(stored("mm_list_w"));
  return saved ? clamp(saved) : START;
}

export function Splitter() {
  const [width, setWidth] = useState(initial);
  const [dragging, setDragging] = useState(false);
  useEffect(() => document.body.style.setProperty("--list-w", `${width}px`), [width]);
  const settle = (w: number) => {
    setWidth(clamp(w));
    store("mm_list_w", String(clamp(w)));
  };
  return (
    <div
      className={dragging ? "splitter drag" : "splitter"}
      id="splitter"
      role="separator"
      aria-orientation="vertical"
      aria-controls="list"
      aria-label="Message list width"
      aria-valuemin={MIN}
      aria-valuemax={MAX}
      aria-valuenow={width}
      tabIndex={0}
      title="Drag to resize · ←/→ · double-click resets"
      onDoubleClick={() => settle(START)}
      onKeyDown={(e) => {
        const to = { ArrowLeft: width - STEP, ArrowRight: width + STEP, Home: MIN, End: MAX }[e.key];
        if (to !== undefined) {
          e.preventDefault();
          e.stopPropagation();
          settle(to);
        }
      }}
      onPointerDown={(e) => {
        e.preventDefault();
        const bar = e.currentTarget, from = e.clientX, was = width, id = e.pointerId;
        bar.setPointerCapture(id);
        setDragging(true);
        const move = (ev: PointerEvent) => setWidth(clamp(was + ev.clientX - from));
        const up = (ev: PointerEvent) => {
          setDragging(false);
          bar.removeEventListener("pointermove", move);
          bar.removeEventListener("pointerup", up);
          bar.removeEventListener("pointercancel", up);
          settle(was + ev.clientX - from);
        };
        bar.addEventListener("pointermove", move);
        bar.addEventListener("pointerup", up);
        bar.addEventListener("pointercancel", up);
      }}
    />
  );
}
