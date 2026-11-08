import type { ReactNode } from "react";
import { createStore, type Store } from "./store";

// A few words at the edge of the page, for a while: a toast, the offer of Undo, a send on its way out. A new float takes
// over from the last and starts its own clock, which runs out as `over` and not as nothing, so what it said can fade with it.
export interface Shown {
  words: ReactNode;
  turn: number;      // a float of its own: another with the same words is still a new one
  life?: number | undefined;     // ms, for one that goes by itself: what can be done about it drains over this
  speak?: string | undefined;    // what a screen reader hears in place of the words, which are for the eye
  over?: boolean;
}

export interface FloatPlace {
  shown: Store<Shown | null>;
  show(words: ReactNode, speak?: string): void;
  clear(): void;
}

// How long a float stands: long enough to read, or to act on.
export const SAYS_MS = 2600, OFFERS_MS = 8000;

export function createFloat(life: number): FloatPlace {
  const shown = createStore<Shown | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let turn = 0;
  return {
    shown,
    show(words, speak) {
      clearTimeout(timer);
      const now: Shown = { words, turn: ++turn, life, speak };
      shown.set(now);
      timer = setTimeout(() => shown.set({ ...now, over: true }), life);
    },
    clear() {
      clearTimeout(timer);
      shown.set(null);
    },
  };
}

// A float, and what can be done about it (`children`). A screen reader reads what lands in a live region that was already
// there, so the region stays when the float goes. A dialog makes what's outside it inert, and a click goes straight through
// that: a float stays in reach over one.
export function Float({ id, className, shown, children }: { id: string; className: string; shown: Shown | null; children?: ReactNode }) {
  const now = shown && !shown.over ? shown : null;
  return (
    <div className="float-live" role="status" aria-live="polite" aria-atomic="true" data-react-aria-top-layer="true">
      {now && (
        <div id={id} key={now.turn} className={className}>
          <span aria-hidden={now.speak ? true : undefined}>{now.words}</span>
          {now.speak && <span className="visually-hidden">{now.speak}</span>}
          {children}
          {now.life ? <div className="float-bar" aria-hidden="true" style={{ animationDuration: `${now.life}ms` }} /> : null}
        </div>
      )}
    </div>
  );
}

const told = createFloat(SAYS_MS);
const spoken = createStore("");

export function toast(message: string) {
  told.show(message);
  spoken.set(message);
}

// For the screen reader alone. Clear, then set: an identical repeat is read again.
export function announce(message: string) {
  spoken.set("");
  setTimeout(() => spoken.set(message), 150);
}

export function Toaster() {
  const shown = told.shown.useValue();
  const live = spoken.useValue();
  return (
    <>
      <div className={shown && !shown.over ? "toast show" : "toast"} id="toast" aria-hidden="true">{shown?.words}</div>
      <div id="srLive" className="visually-hidden" aria-live="polite" aria-atomic="true" data-live-announcer="true">{live}</div>
    </>
  );
}
