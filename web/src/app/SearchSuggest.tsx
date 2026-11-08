// What the search box offers as you type: the word you're on, finished by the server from the grammar and from who has written
// to you. Debounced, and only ever a read of what is stored. The composer's address field is the same list under another field.
import { useEffect, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { get } from "../lib/api/client";
import type { GetAnswers } from "../lib/api/types";

export type Offer = GetAnswers["/api/suggest"]["items"][number] & { conversation?: { id: string; account: string } };

interface Box { left: number; top: number; width: number }

const WAIT = 140;
const LIST = "searchSuggest";

export function useSuggestions(input: RefObject<HTMLInputElement | null>) {
  const [items, setItems] = useState<Offer[]>([]);
  const [box, setBox] = useState<Box | null>(null);
  const [at, setAt] = useState(-1);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const asked = useRef(0);       // a newer word, or the list closing, makes an answer on its way stale
  useEffect(() => () => clearTimeout(timer.current), []);
  const close = () => {
    clearTimeout(timer.current);
    asked.current++;
    setItems([]);
    setAt(-1);
  };
  // `first` goes above whatever the server says: what only the page knows of.
  const lookUp = (value: string, first?: Offer) => {
    close();
    if (!value.trim()) return;
    const seq = ++asked.current;
    timer.current = setTimeout(async () => {
      if (document.activeElement !== input.current) return;
      let said: Offer[];
      try {
        said = (await get(`/api/suggest?q=${encodeURIComponent(value)}`)).items;
      } catch {
        return;
      }
      if (seq !== asked.current || document.activeElement !== input.current) return;
      const r = input.current!.getBoundingClientRect();
      setBox({ left: r.left, top: r.bottom + 3, width: Math.max(r.width, 300) });
      setItems(first ? [first, ...said] : said);
      setAt(-1);
    }, WAIT);
  };
  // The list's own keys, while it's showing. Whether it took this one.
  const onKey = (e: KeyboardEvent<HTMLInputElement>, take: (offer: Offer, run: boolean) => void) => {
    if (!items.length) return false;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const by = e.key === "ArrowDown" ? 1 : -1;
      setAt((at + by + items.length + (at < 0 && by < 0 ? 1 : 0)) % items.length);
      return true;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();       // Esc takes the list away, not the search
      close();
      return true;
    }
    if ((e.key === "Enter" || e.key === "Tab") && at >= 0) {
      e.preventDefault();
      take(items[at]!, e.key === "Enter");
      return true;
    }
    return false;
  };
  return { items, box, at, lookUp, close, onKey, listId: items.length ? LIST : undefined, activeId: at >= 0 ? `${LIST}-${at}` : undefined };
}

export function SuggestMenu({ box, items, at, take }: { box: Box | null; items: Offer[]; at: number; take: (offer: Offer, run: boolean) => void }) {
  if (!box || !items.length) return null;
  return (
    // eslint-disable-next-line jsx-a11y/interactive-supports-focus -- focus stays on the search box, which takes the keys and names this through aria-activedescendant
    <div className="ac-menu" id={LIST} role="listbox" aria-label="Search suggestions" style={box} onMouseDown={(e) => e.preventDefault()}>
      {items.map((it, i) => (
        // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/interactive-supports-focus -- focus stays on the search box, which takes the keys and names this through aria-activedescendant
        <div key={`${i}:${it.value}`} id={`${LIST}-${i}`} role="option" aria-selected={i === at} className={i === at ? "ac-item sel" : "ac-item"} onClick={() => take(it, true)}>
          <span className="ac-nm">{it.label}</span>{it.detail && <span className="ac-ad">{it.detail}</span>}
        </div>
      ))}
    </div>
  );
}
