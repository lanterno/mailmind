// Back closes what is on top. While anything dismissible is open the tab holds one extra history entry, and Back spends it
// on the top layer; a layer closed some other way gives the entry back, so the next Back isn't swallowed unwinding a dead
// one. A traversal is asynchronous, so while one is on its way nothing may push: its entry would land first and the
// traversal would undo it instead.
import { useEffect, useRef } from "react";

interface Layer { close: () => void | false }     // false: it took Back for something smaller and is still open

const layers: Layer[] = [];
let armed = false, returning = false, listening = false;
const waiting: (() => void)[] = [];

const onTrapEntry = () => Boolean((history.state as { mmTrap?: number } | null)?.mmTrap);

function sync() {
  if (returning) return;
  if (layers.length && !armed) {
    armed = true;
    history.pushState({ mmTrap: 1 }, "", location.href);
  } else if (!layers.length && armed) {
    armed = false;
    if (onTrapEntry()) {
      returning = true;
      history.back();
    }
  }
}

function onPop() {
  if (returning) {
    returning = false;
    for (const run of waiting.splice(0)) run();
    sync();
    return;
  }
  armed = false;     // Back spent the entry
  const top = layers[layers.length - 1];
  if (!top) return;
  if (top.close() !== false) layers.pop();
  sync();
}

// Going somewhere from inside a layer takes the place of the trap entry, or it would sit under the new page as one Back
// that does nothing.
export function navigate(apply: (replace: boolean) => void) {
  const run = () => apply(onTrapEntry());
  if (returning) waiting.push(run);
  else run();
}

export function useLayer(open: boolean, close: () => void | false) {
  const latest = useRef(close);
  useEffect(() => {
    latest.current = close;
  });
  useEffect(() => {
    if (!open) return;
    if (!listening) {
      listening = true;
      window.addEventListener("popstate", onPop);
    }
    const layer: Layer = { close: () => latest.current() };
    layers.push(layer);
    sync();
    return () => {
      const i = layers.indexOf(layer);
      if (i >= 0) layers.splice(i, 1);
      sync();
    };
  }, [open]);
}
