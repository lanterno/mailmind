// ⌘K: everything you can do from here, found by typing.
import { useRef, useState } from "react";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { rankByLabel } from "../lib/fuzzy";
import { createStore } from "../lib/store";
import { toast } from "../lib/toast";
import type { Command } from "./commands";
import { useLayer } from "../lib/backTrap";

export const paletteOpen = createStore(false);

function Entries({ commands, close }: { commands: Command[]; close: () => void }) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(0);
  const typed = query.trim();
  const shown = rankByLabel(typed, commands);
  const at = Math.min(picked, Math.max(0, shown.length - 1));
  const list = useRef<HTMLDivElement>(null);
  const run = (c: Command | undefined) => {
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
    list.current?.querySelector(`#pi-${next}`)?.scrollIntoView({ block: "nearest" });
  };
  let group = "";
  return (
    <div className="pbox">
      <input
        className="pq"
        id="pq"
        autoFocus
        type="text"
        placeholder="Jump to a folder, run a command…  (↑↓ Enter)"
        autoComplete="off"
        role="combobox"
        aria-label="Command"
        aria-expanded="true"
        aria-controls="plist"
        aria-activedescendant={shown.length ? `pi-${at}` : undefined}
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
      <div className="plist" id="plist" role="listbox" aria-label="Commands" ref={list}>
        {!shown.length && <div className="pnone">No matches.</div>}
        {shown.map((c, i) => {
          const heading = !typed && c.group !== group ? <div key={`g-${c.group}`} className="pgrp" role="presentation">{c.group}</div> : null;
          group = c.group;
          return [
            heading,
            <div key={c.id} className={i === at ? "pi sel" : "pi"} role="option" aria-selected={i === at} id={`pi-${i}`} onClick={() => run(c)} onMouseMove={() => setPicked(i)}>
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
          <Entries commands={commands} close={close} />
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
