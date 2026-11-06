// The bar across the top: the rail's control, search, and the way back to the current app. Each keeps its state in the
// body's classes and the storage keys the current page uses, since the two share a stylesheet and a browser.
import { useEffect, useRef, useState } from "react";
import { composing } from "../features/compose/compose";
import { OutboxButton } from "../features/compose/Outbox";
import { createStore, store, stored } from "../lib/store";
import { nextTheme, setTheme, theme, THEME_LABEL } from "../lib/theme";
import { Icon } from "../lib/ui/Icon";
import { go, type ListSource } from "./route";

// Wide windows: the control switches the rail between icons and icons with names. Narrower: it opens it as a drawer.
const docked = matchMedia("(min-width:1101px)");
const railWide = createStore(stored("mm_rail") === "wide");
const railOpen = createStore(false);

export function toggleRail() {
  if (docked.matches) {
    const wide = !railWide.get();
    railWide.set(wide);
    store("mm_rail", wide ? "wide" : "narrow");
  } else railOpen.set(!railOpen.get());
}
export const closeRailDrawer = () => railOpen.set(false);

function useDocked() {
  const [is, setIs] = useState(docked.matches);
  useEffect(() => {
    const change = () => {
      setIs(docked.matches);
      closeRailDrawer();
    };
    docked.addEventListener("change", change);
    return () => docked.removeEventListener("change", change);
  }, []);
  return is;
}

function RailToggle() {
  const isDocked = useDocked();
  const wide = railWide.useValue();
  const open = railOpen.useValue();
  useEffect(() => void document.body.classList.toggle("rail-wide", wide), [wide]);
  useEffect(() => void document.body.classList.toggle("rail-open", open), [open]);
  const label = isDocked ? (wide ? "Show folder icons only" : "Show folder names") : "Folders";
  return (
    <button className="railtoggle" id="railToggle" title="Folders — icons ⇄ names ([)" aria-label={label} aria-controls="rail" aria-expanded={isDocked ? wide : open} onClick={toggleRail}>
      <Icon name="sidebar" />
    </button>
  );
}

export function RailScrim() {
  const open = railOpen.useValue();
  return <div className={open ? "scrim on" : "scrim"} onClick={closeRailDrawer} />;
}

export function TopBar({ source, readOnly }: { source: ListSource | null; readOnly: boolean }) {
  const current = theme.useValue();
  const input = useRef<HTMLInputElement>(null);
  const query = source?.kind === "search" ? source.q : "";
  const [text, setText] = useState(query);
  const [shown, setShown] = useState(query);
  const [folded, setFolded] = useState(true);
  if (query !== shown) {     // the URL moved (Back, a rail click): the box follows it
    setShown(query);
    setText(query);
  }
  useEffect(() => {
    document.body.classList.toggle("search-open", !folded);
    if (!folded) input.current?.focus();     // only now is it on screen
  }, [folded]);
  const home: ListSource | null = source?.kind === "search"
    ? source.account ? { kind: "folder", account: source.account, folder: "INBOX" } : { kind: "allInboxes" }
    : null;
  const account = source?.kind === "folder" || source?.kind === "search" ? source.account : "";
  const leave = () => {
    setText("");
    setFolded(true);
    input.current?.blur();
    if (home) go({ source: home, opened: null });
  };
  return (
    <header className="top">
      <div className="r1">
        <RailToggle />
        <div className="brand">
          <svg viewBox="0 0 32 32" aria-hidden="true" focusable="false">
            <circle cx="24.5" cy="7.5" r="3.2" fill="var(--sun)" />
            <path d="M3 18.5 17.5 9.5 14 19z" fill="var(--primary)" />
            <path d="M3 18.5 14 19l14.5-8L19 24.5z" fill="color-mix(in srgb,var(--primary) 62%,var(--bg))" />
            <path d="M14 19 19 24.5l-5.2 2.2z" fill="color-mix(in srgb,var(--primary) 82%,black)" />
          </svg>
          <span>Mail<b>Mind</b></span>
        </div>
        <input
          ref={input}
          className="search"
          id="search"
          placeholder="Search — is:unread, from:amazon"
          aria-label="Search"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") leave();
            else if (e.key === "Enter") {
              const q = text.trim();
              if (q) go({ source: { kind: "search", q, account }, opened: null });
              else leave();
            }
          }}
        />
        <button className="tbtn search-x" onMouseDown={(e) => e.preventDefault()} onClick={leave}>Cancel</button>
        <button
          className="tbtn icon search-tog"
          title="Search (/)"
          aria-label="Search"
          onClick={() => setFolded(false)}
        >
          <Icon name="search" />
        </button>
        <div className="tools">
          <button className="tbtn" id="composeBtn" title="Compose (c)" aria-label="Compose" onClick={() => composing.open({ mode: "new" })}><Icon name="pen" /><span className="tb-lbl"> Compose</span><kbd className="tb-kbd">c</kbd></button>
          <OutboxButton readOnly={readOnly} />
          <a className="tbtn" href="/" title="The current app, with everything this one doesn't do yet">Current app</a>
          <button className="tbtn icon" id="themeBtn" aria-label={`Theme: ${THEME_LABEL[current]} — switch to ${THEME_LABEL[nextTheme(current)]}`} title="Day, Dusk or Night" onClick={() => setTheme(nextTheme(current))}>
            <Icon name={current === "day" ? "sun" : current === "dusk" ? "dusk" : "moon"} />
          </button>
        </div>
      </div>
    </header>
  );
}
