// ⌘K: everything you can do from here, found by typing.
import { useRef, useState } from "react";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { rankByLabel } from "../lib/fuzzy";
import { createStore } from "../lib/store";
import { toast } from "../lib/toast";
import type { Command } from "./commands";
import { useLayer } from "../lib/backTrap";

export const paletteOpen = createStore(false);

// One thing to pick. A group of "" shows no heading.
export interface Entry { id: string; group: string; label: string; hint?: string; run: () => void }

// A box to type in over a list of things to pick, found by typing: the palette, and the folder a move goes to. `idPrefix` keeps
// two apart, since ⌘K can open over another.
export function Entries({ entries, close, placeholder, label, listLabel, idPrefix }: { entries: Entry[]; close: () => void; placeholder: string; label: string; listLabel: string; idPrefix: string }) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(0);
  const typed = query.trim();
  const shown = rankByLabel(typed, entries);
  const at = Math.min(picked, Math.max(0, shown.length - 1));
  const list = useRef<HTMLDivElement>(null);
  const run = (c: Entry | undefined) => {
    close();     // first, so a command that opens something gets the focus
    if (!c) return;
    try {
      c.run();
    } catch {
      toast("Couldn’t run that");
    }
  };
  const step = (by: number) => {
    const next = (at + by + shown.length) % Math.max(1, shown.length);
    setPicked(next);
    list.current?.querySelector(`#${idPrefix}i-${next}`)?.scrollIntoView({ block: "nearest" });
  };
  return (
    <div className="pbox">
      <input
        className="pq"
        id={`${idPrefix}q`}
        // eslint-disable-next-line jsx-a11y/no-autofocus -- the palette was opened this moment, to type in
        autoFocus
        type="text"
        placeholder={placeholder}
        autoComplete="off"
        role="combobox"
        aria-label={label}
        aria-expanded="true"
        aria-controls={`${idPrefix}list`}
        aria-activedescendant={shown.length ? `${idPrefix}i-${at}` : undefined}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setPicked(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || (e.ctrlKey && e.key === "n")) {
            e.preventDefault();
            step(1);
          } else if (e.key === "ArrowUp" || (e.ctrlKey && e.key === "p")) {
            e.preventDefault();
            step(-1);
          } else if (e.key === "Enter") {
            e.preventDefault();
            run(shown[at]);
          } else if (e.key === "Tab") e.preventDefault();
        }}
      />
      <div className="plist" id={`${idPrefix}list`} role="listbox" aria-label={listLabel} ref={list}>
        {!shown.length && <div className="pnone">No matches.</div>}
        {shown.map((c, i) => {
          const heading = !typed && c.group !== (shown[i - 1]?.group ?? "") ? <div key={`g-${c.group}`} className="pgrp" role="presentation">{c.group}</div> : null;
          return [
            heading,
            // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/interactive-supports-focus -- focus stays on the field, which takes the keys and names this through aria-activedescendant
            <div key={c.id} className={i === at ? "pi sel" : "pi"} role="option" aria-selected={i === at} id={`${idPrefix}i-${i}`} onClick={() => run(c)} onMouseMove={() => setPicked(i)}>
              <span className="pi-l">{c.label}</span>{c.hint && <kbd>{c.hint}</kbd>}
            </div>,
          ];
        })}
      </div>
    </div>
  );
}

export function Palette({ commands }: { commands: Command[] }) {
  const open = paletteOpen.useValue();
  const close = () => paletteOpen.set(false);
  useLayer(open, close);
  return (
    <ModalOverlay className="palette show" isOpen={open} isDismissable onOpenChange={(o) => { if (!o) close(); }}>
      <Modal>
        <Dialog aria-label="Command palette" style={{ outline: "none" }}>
          <Entries entries={commands} close={close} placeholder="Jump to a folder, run a command…  (↑↓ Enter)" label="Command" listLabel="Commands" idPrefix="p" />
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
