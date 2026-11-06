// The app's one view: the rail, the list and the reader, and the keys that work across them.
import { useEffect, useRef } from "react";
import { post } from "../api/client";
import { composing } from "../features/compose/compose";
import { asking, stopAsking } from "../features/ask/ask";
import { AskPane } from "../features/ask/AskPane";
import { DoPanel } from "../features/ask/Do";
import { backFromDigest, BriefRow } from "../features/brief/Brief";
import { lookedAt, NewMailPill } from "../features/brief/NewMail";
import { Panels } from "../features/brief/Panels";
import { ComposerModal, SendFloatView } from "../features/compose/Composer";
import { clearMarks, undo, undoFloat, type BulkVerb } from "../features/mailbox/actions";
import { FolderRail } from "../features/mailbox/FolderRail";
import { MessageList } from "../features/mailbox/MessageList";
import { WIDTHS, readPrefs } from "../features/mailbox/readPrefs";
import { Reader, readFocus, setReadFocus } from "../features/mailbox/Reader";
import type { InboxTab } from "../features/mailbox/rows";
import { snoozeAsk, SnoozeMenu } from "../features/mailbox/SnoozeMenu";
import { setGrouping, setInboxTab, useMailbox, type Mailbox } from "../features/mailbox/useMailbox";
import { ConfirmDialog } from "../lib/confirm";
import { OverLimitDialog } from "../lib/overLimit";
import { stored } from "../lib/store";
import { shortAddress } from "../lib/text";
import { toast, Toaster } from "../lib/toast";
import { Icon } from "../lib/ui/Icon";
import { buildCommands, commandForKey, goChord, type Command } from "./commands";
import { Palette, paletteOpen } from "./Palette";
import { newBuild, useRealtime } from "./realtime";
import { go, type ListSource } from "./route";
import { Splitter } from "./Splitter";
import { clearSearchBox, closeRailDrawer, RailScrim, TopBar, toggleRail } from "./TopBar";
import { useQueryClient } from "@tanstack/react-query";

const TABS: [InboxTab, string][] = [["needs", "Needs you"], ["all", "Everything"], ["unread", "Unread"]];
const GO_HINT = "Go to folder:  i·s·a·t·k·d·z·w·l";
const narrow = () => matchMedia("(max-width:820px)").matches;
const sourceWhere = (s: ListSource | null) => JSON.stringify(s);

function Floats() {
  const qc = useQueryClient();
  const label = undoFloat.useValue();
  const build = newBuild.useValue();
  return (
    <>
      <SendFloatView />
      <div className="send-float undo-float" id="undoFloat" hidden={!label}>
        <span>{label}</span><button id="ufUndo" onClick={() => void undo(qc)}>Undo · <kbd>z</kbd></button>
      </div>
      <div className="send-float build-float" id="buildFloat" role="status" hidden={!build}>
        MailMind was updated. <button id="buildReload" onClick={() => location.reload()}>Reload</button>
      </div>
    </>
  );
}

function BulkBar({ m }: { m: Mailbox }) {
  const n = m.marks.size;
  if (!n) return null;
  const verb = (name: string, v: BulkVerb, icon?: string, danger?: boolean) => (
    <button className={danger ? "bb bb-del" : "bb"} onClick={() => m.bulk(v)}>{icon && <Icon name={icon} />} {name}</button>
  );
  return (
    <div className="bulkbar" id="bulkbar">
      <span className="bcount">{n} selected</span>
      {verb("Archive", "archive", "archive")}
      {verb("Delete", "trash", "trash", true)}
      {verb("Mark read", "read")}
      {verb("Mark unread", "unread")}
      {verb("Star", "star", "star")}
      {verb("Junk", "junk", "ban", true)}
      <button className="bb" onClick={(e) => snoozeAsk.set({ target: { pks: [...m.marks] }, rows: [...m.marks], anchor: e.currentTarget, canUnsnooze: false })}><Icon name="clock" /> Snooze</button>
      {verb("Mute", "mute", "bell-off")}
      <button className="bclear" onClick={clearMarks}>Clear</button>
    </div>
  );
}

async function saveSearch(m: Mailbox) {
  if (m.source?.kind !== "search") return;
  const q = m.source.q.trim();
  const name = (window.prompt("Name this view:", q.slice(0, 40)) ?? "").trim();
  if (!q || !name) return;
  try {
    const d = await post("/api/views", { verb: "create", name, q });
    if (!d.ok) {
      toast(d.msg || "Couldn’t save");
      return;
    }
    void m.qc.invalidateQueries({ queryKey: ["views"] });
    toast(`Saved view · ${name}`);
  } catch {
    toast("Couldn’t save view");
  }
}

// The keys. Nothing here fires while you type or while a menu or dialog has the focus, and a browser's own chords are left
// alone, but ⌘K. The help overlay's setting (mm_nokeys) turns single keys off, as WCAG 2.1.4 asks.
function useKeys(m: Mailbox, commands: Command[]) {
  const chord = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (composing.isOpen()) return;   // it has keys of its own
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        paletteOpen.set(!paletteOpen.get());
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const at = e.target as Element | null;
      if (at?.closest?.('input,textarea,select,[contenteditable="true"],[role="menu"],[role="dialog"],[role="alertdialog"]')) return;
      if (at?.closest?.('[role="separator"]') && e.key.startsWith("Arrow")) return;
      if (stored("mm_nokeys") === "1" && e.key !== "Escape") return;
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (chord.current !== null) {
        clearTimeout(chord.current);
        chord.current = null;
        e.preventDefault();
        goChord(m, key);
        return;
      }
      if (key === "g") {
        e.preventDefault();
        toast(GO_HINT);
        chord.current = setTimeout(() => { chord.current = null; }, 1800);
        return;
      }
      if (key === "Escape") {
        if (asking.get()) {
          stopAsking();
          clearSearchBox();
        } else if (readFocus.get()) setReadFocus(false);
        else if (m.marks.size) clearMarks();
        else if (m.opened || m.briefAsked) m.close();
        else if (m.source?.kind === "digest") go({ source: backFromDigest(m.accounts), opened: null });
        else if (m.source?.kind === "search") go({ source: m.source.account ? { kind: "folder", account: m.source.account, folder: "INBOX" } : { kind: "allInboxes" }, opened: null });
        else return;
        e.preventDefault();
        return;
      }
      if (key === "Enter" && at?.closest?.("button,a[href]")) return;   // Enter on a control presses that control, nothing more
      if (key === "Enter" && narrow()) return;
      const command = commandForKey(commands, key);
      if (command) {
        e.preventDefault();
        command.run();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });
}

export function App() {
  useRealtime();
  const m = useMailbox();
  const { source, opened, accounts, aliases, all, rows, tab, list, folder, inInbox, searching, title } = m;
  const prefs = readPrefs.useValue();
  const asked = asking.useValue();

  // /next on its own opens the first account's Inbox.
  useEffect(() => {
    if (!source && accounts[0]) go({ source: { kind: "folder", account: accounts[0], folder: "INBOX" }, opened: null }, { replace: true });
  }, [source, accounts[0]]);
  const reading = Boolean(opened) || m.briefAsked;
  useEffect(() => void document.body.classList.toggle("reading", reading), [reading]);
  useEffect(closeRailDrawer, [source]);
  useEffect(() => clearMarks(), [source]);
  useEffect(() => {
    if (source?.kind === "search") return;
    stopAsking();
  }, [sourceWhere(source)]);
  useEffect(() => {
    const look = () => { if (!document.hidden) lookedAt(source, accounts); };
    look();
    document.addEventListener("visibilitychange", look);
    return () => document.removeEventListener("visibilitychange", look);
  }, [source, accounts.join()]);
  useEffect(() => document.body.style.setProperty("--measure", `${WIDTHS[prefs.width]}px`), [prefs.width]);

  const commands = buildCommands(m, { toggleRail, saveSearch: () => void saveSearch(m) });
  useKeys(m, commands);

  const pages = list.data?.pages ?? [];
  const last = pages[pages.length - 1];
  const more = list.hasNextPage ? "+" : "";
  const noun = m.grouped && !searching && m.source?.kind !== "digest" ? "conversation" : "message";
  const count = searching
    ? `${all.length}${more} result${all.length === 1 && !more ? "" : "s"}`
    : `${all.length}${more} ${noun}${all.length === 1 && !more ? "" : "s"}${last?.syncing || last?.catchingUp ? " · reading the rest…" : ""}`;

  let empty: React.ReactNode = <div className="nores">{searching ? "No matches." : m.source?.kind === "digest" ? "Nothing left here." : "Nothing here."}</div>;
  if (m.digest && !accounts.length) empty = <div className="nores firstrun"><div className="fr-h">Welcome to MailMind</div><div className="fr-b">No email accounts connected yet. Connect one in the <a href="/">current app</a>.</div></div>;
  else if (list.isError) empty = <div className="nores" role="status">Couldn’t load this list. <button className="ai-rerun" onClick={() => void list.refetch()}>Retry</button></div>;
  else if (list.isPending && source) empty = <div className="nores" role="status">Loading…</div>;
  else if (last?.error) empty = <div className="nores">{last.error}</div>;
  else if (inInbox && !searching && tab !== "all" && all.length) empty = <div className="nores">{tab === "needs" ? "Nothing needs you." : "Nothing unread."} Everything else is in <button className="linkbtn" onClick={() => setInboxTab("all")}>Everything</button>.</div>;
  else if (last?.syncing) empty = <div className="nores" role="status">Reading this folder for the first time…</div>;
  else if (last?.syncFailed) empty = <div className="nores" role="status">Couldn’t sync this folder. <button className="ai-rerun" onClick={() => void list.refetch()}>Retry</button></div>;

  const counts: Record<InboxTab, string> = {
    needs: String(all.filter((r) => r.needsYou).length),
    all: `${all.length}${more}`,
    unread: String(all.filter((r) => !r.seen).length),
  };
  return (
    <>
      <TopBar source={source} readOnly={m.digest?.profile === "read-only"} />
      <main className="split">
        <FolderRail accounts={accounts} aliases={aliases} source={source} />
        <Splitter />
        <section className="list" id="list">
          <div className="listhead">
            <div className="lh1">
              <h1 className="lh-title" id="listTitle">{title}</h1>
              {!searching && (
                <button className={m.grouped ? "thread-toggle on" : "thread-toggle"} id="threadToggle" title="Group into conversations" onClick={() => setGrouping(!m.grouped)}>
                  <Icon name={m.grouped ? "chat" : "list"} /> {m.grouped ? "Conversations" : "Messages"}
                </button>
              )}
            </div>
            {searching && source?.kind === "search" && (
              <div className="client-ctx" id="clientCtx">
                <Icon name="search" /> <b>{source.q}</b> · {all.length} · all accounts ·{" "}
                <button className="ctx-save" title="Save as a view (v)" onClick={() => void saveSearch(m)}>+ Save view</button>
              </div>
            )}
            {m.source?.kind === "digest" && (
              <div className="client-ctx" id="clientCtx">
                <Icon name="wind" /> <b>{title}</b> · from today’s brief ·{" "}
                <button className="ctx-save" onClick={() => go({ source: backFromDigest(accounts), opened: null })}>‹ Inbox</button>
              </div>
            )}
            {inInbox && !searching && (
              <div className="lh-tabs" id="lhTabs" role="tablist" aria-label="Inbox">
                {TABS.map(([id, label], i) => (
                  <button
                    key={id}
                    className="lh-tab"
                    role="tab"
                    id={`tab-${id}`}
                    aria-selected={tab === id}
                    aria-controls="rows"
                    tabIndex={tab === id ? 0 : -1}
                    onClick={() => setInboxTab(id)}
                    onKeyDown={(e) => {
                      const by = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                      if (!by) return;
                      e.preventDefault();
                      e.stopPropagation();
                      const next = TABS[(i + by + TABS.length) % TABS.length]![0];
                      setInboxTab(next);
                      document.getElementById(`tab-${next}`)?.focus();
                    }}
                  >
                    {label}<span className={id === "needs" && counts.needs !== "0" ? "n sun" : "n"}>{counts[id]}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="lh-row"><span id="count">{asked ? (asked.state === "loading" ? "asking…" : asked.state === "answer" ? `${asked.answer.sources?.length ?? 0} source${asked.answer.sources?.length === 1 ? "" : "s"}` : "") : list.isSuccess ? count : "—"}</span></div>
          </div>
          <BulkBar m={m} />
          <NewMailPill />
          {asked ? <AskPane at={asked} m={m} /> : <MessageList
            above={inInbox && !searching ? <BriefRow m={m} /> : null}
            rows={rows}
            openPk={m.openPk}
            marks={m.marks}
            label={title}
            folder={folder}
            isWhole={m.isWhole}
            accountChip={(r) => (source?.kind === "folder" ? null : aliases[r.account] || shortAddress(r.account))}
            onOpen={m.open}
            onFile={m.file}
            empty={empty}
            footer={list.hasNextPage ? <button className="load-more" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>{searching ? "Load more results" : "Load 200 more"}</button> : null}
          />}
        </section>
        <Reader m={m} />
      </main>
      <RailScrim />
      <SnoozeMenu onSnooze={m.snooze} onUnsnooze={m.unsnooze} />
      <Palette commands={commands} />
      <ComposerModal
        senders={m.digest?.send_accounts?.length ? m.digest.send_accounts : accounts}
        aliases={aliases}
        ai={m.digest?.ai?.engine !== "none"}
      />
      <Panels m={m} />
      <DoPanel account={source?.kind === "folder" || source?.kind === "search" ? source.account : ""} />
      <OverLimitDialog />
      <ConfirmDialog />
      <Floats />
      <Toaster />
    </>
  );
}
