// The app's one view: the rail, the list and the reader, and the keys that work across them.
import { useEffect, useEffectEvent, useRef } from "react";
import { post, refusalOf } from "../lib/api/client";
import { copyId, copyOf } from "../lib/copy";
import { composing } from "../features/compose/compose";
import { asking, keepLiteral, stopAsking, translating } from "../features/assistant/ask";
import { AskPane } from "../features/assistant/AskPane";
import { DoPanel } from "../features/assistant/Do";
import { backFromDigest, BriefRow } from "../features/assistant/Brief";
import { cameBack, lookedAt, NewMailPill } from "../features/assistant/NewMail";
import { Panels } from "../features/assistant/Panels";
import { ComposerModal, SendFloatView } from "../features/compose/Composer";
import { clearMarks, readOnly, undo, undoFloat, type BulkVerb } from "../features/mailbox/actions";
import { searchScope, setSearchScope } from "../features/mailbox/data";
import { FirstRead } from "../features/mailbox/FirstRead";
import { FolderRail } from "../features/mailbox/FolderRail";
import { MessageList } from "../features/mailbox/MessageList";
import { WIDTHS, readPrefs } from "../features/mailbox/readPrefs";
import { Reader, readFocus, setReadFocus } from "../features/mailbox/Reader";
import type { InboxTab } from "../features/mailbox/rows";
import { snoozeAsk, SnoozeMenu } from "../features/attention/SnoozeMenu";
import { setGrouping, setInboxTab, useMailbox, type Mailbox } from "../features/mailbox/useMailbox";
import { useSwipe } from "../features/mailbox/swipe";
import { Curate } from "../features/curation/Curate";
import { filterForm, newFilter } from "../features/curation/open";
import { Hub } from "../features/discovery/Docs";
import { FeedRiver, PaperTrail } from "../features/discovery/Lanes";
import { ScreenerPane, useScreener } from "../features/curation/Screener";
import { settingsOpen } from "../features/settings";
import { helpOpen } from "../features/status/Help";
import { Guide, guideOpen } from "../features/status/Guide";
import { Settings } from "../features/settings/Settings";
import { Help } from "../features/status/Help";
import { badAccounts } from "../features/status/model";
import { AccountAlert } from "../features/status/Status";
import { ConfirmDialog } from "../lib/confirm";
import { OverLimitDialog } from "../lib/overLimit";
import { singleKeys } from "../lib/prefs";
import { shortAddress } from "../lib/text";
import { Float, toast, Toaster } from "../lib/toast";
import { Icon } from "../lib/ui/Icon";
import { buildCommands, commandForKey, goChord, goKeys, legendOf, onSavedView, type Command } from "./commands";
import { MoveTo } from "./MoveTo";
import { Palette, paletteOpen } from "./Palette";
import { newBuild, useRealtime } from "./realtime";
import { go, type ListSource } from "./route";
import { Splitter } from "./Splitter";
import { clearSearchBox, closeRailDrawer, openSearchBar, RailScrim, TopBar, toggleRail } from "./TopBar";
import { useQueryClient } from "@tanstack/react-query";

const TABS: [InboxTab, string][] = [["needs", "Needs you"], ["all", "Everything"], ["unread", "Unread"]];
const goHint = () => `Go to folder:  ${goKeys().map((g) => g.key).join("·")}`;
const narrow = () => matchMedia("(max-width:820px)").matches;
const sourceWhere = (s: ListSource | null) => JSON.stringify(s);

function Floats() {
  const qc = useQueryClient();
  const offer = undoFloat.shown.useValue();
  const build = newBuild.useValue();
  return (
    <>
      <SendFloatView />
      <Float id="undoFloat" className="send-float undo-float" shown={offer}>
        <button id="ufUndo" onClick={() => void undo(qc)}>Undo · <kbd>z</kbd></button>
      </Float>
      <div id="swipeBadge" aria-hidden="true"><span className="sw-archive"><Icon name="archive" /> Archive</span><span className="sw-snooze"><Icon name="clock" /> Tomorrow</span></div>
      <div className="send-float build-float" id="buildFloat" role="status" data-react-aria-top-layer="true" hidden={!build}>
        MailMind was updated or restarted. <button id="buildReload" onClick={() => location.reload()}>Reload</button>
      </div>
    </>
  );
}

function ScopeToggle({ account }: { account: string }) {
  const scope = searchScope.useValue();
  if (!account) return <>all accounts</>;
  return <button type="button" className="scopetog" id="scopeTog" onClick={() => setSearchScope(scope === "all" ? "account" : "all")}>{scope === "all" ? "all accounts" : "this account"} ▾</button>;
}

// A search held to the conversation being read, or the way to hold it to that. Where it is held, that's the whole scope, in place
// of the account's: a conversation is in one account.
function ConversationScope({ m, source }: { m: Mailbox; source: Extract<ListSource, { kind: "search" }> }) {
  if (source.thread) return <button type="button" className="scopetog" id="scopeThread" aria-label="Search this account instead of only this conversation" onClick={() => go({ source: { kind: "search", q: source.q, account: source.account }, opened: m.opened })}>this conversation only ✕</button>;
  if (!m.conversation) return null;
  const { id, account } = m.conversation;
  return <> · <button type="button" className="scopetog" id="scopeThread" onClick={() => go({ source: { kind: "search", q: source.q, account, thread: id }, opened: m.opened })}>this conversation only</button></>;
}

// A sentence in the box is searched as written, then as the search it was translated into. The chip says which you're looking at,
// and takes the translation back.
function TranslatedChip({ source }: { source: Extract<ListSource, { kind: "search" }> }) {
  const going = translating.useValue();
  if (going === source.q) return <span className="ctx-chip" role="status"><span className="spin">↻</span> Translating…</span>;
  const words = source.asked;
  if (!words) return null;
  return (
    <span className="ctx-chip" id="translatedChip">
      <Icon name="wind" /> Translated from “{words}”
      <button type="button" aria-label={`Search for “${words}” as typed instead`} onClick={() => {
        keepLiteral(words);
        go({ source: { kind: "search", q: words, account: source.account }, opened: null });
      }}>×</button>
    </span>
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
      <button className="bb" onClick={(e) => snoozeAsk.set({ target: { copies: m.markedRows.map(copyOf) }, rows: [...m.marks], anchor: e.currentTarget, canUnsnooze: false })}><Icon name="clock" /> Snooze</button>
      <button className="bb" id="bbMove" onClick={m.askMove}><Icon name="folder" /> Move</button>
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
    await post("/api/views", { verb: "create", name, q });
    void m.qc.invalidateQueries({ queryKey: ["views"] });
    toast(`Saved view · ${name}`);
  } catch (e) {
    const no = refusalOf(e);
    toast(no ? no.detail || "Couldn’t save" : "Couldn’t save view");
  }
}

// A search as a filter: where it is held to one account, the filter is too.
function createFilter(m: Mailbox) {
  if (m.source?.kind !== "search") return;
  const { q, account } = m.source;
  filterForm.set(newFilter(account && searchScope.get() === "account" ? `${q} account:${account}` : q));
}

// The keys. Nothing here fires while you type or while a menu or dialog has the focus, and a browser's own chords are left
// alone, but ⌘K. The help overlay's setting (mm_nokeys) turns single keys off, as WCAG 2.1.4 asks.
function useKeys(m: Mailbox, commands: Command[]) {
  const chord = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (composing.isOpen() || guideOpen.get()) return;   // each has keys of its own
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        paletteOpen.set(!paletteOpen.get());
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const at = e.target as Element | null;
      if (settingsOpen.get() && !at?.closest?.("input,textarea,select")) {
        if (e.key === "," || e.key === "g") {
          e.preventDefault();
          settingsOpen.set(false);
          return;
        }
        if (e.key === "?") {
          e.preventDefault();
          helpOpen.set(!helpOpen.get());
          return;
        }
      }
      if (at?.closest?.('input,textarea,select,[contenteditable="true"],[role="menu"],[role="dialog"],[role="alertdialog"]')) return;
      if (at?.closest?.('[role="separator"]') && e.key.startsWith("Arrow")) return;
      if (!singleKeys.get() && e.key !== "Escape" && e.key !== "?") return;
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
        toast(goHint());
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
        else if (m.source?.kind === "digest" || m.source?.kind === "documents") go({ source: backFromDigest(m.accounts), opened: null });
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
  const focused = readFocus.useValue();

  // `/` on its own opens the first account's Inbox.
  const firstAccount = accounts[0];
  useEffect(() => {
    if (!source && firstAccount) go({ source: { kind: "folder", account: firstAccount, folder: "INBOX" }, opened: null }, { replace: true });
  }, [source, firstAccount]);
  const isReadOnly = m.digest?.profile === "read-only";
  useEffect(() => readOnly.set(isReadOnly), [isReadOnly]);   // what filing says: it waits for a full run
  const reading = Boolean(opened) || m.briefAsked;
  useEffect(() => void document.body.classList.toggle("reading", reading), [reading]);
  useEffect(closeRailDrawer, [source]);
  useEffect(() => { if (focused) closeRailDrawer(); }, [focused]);
  useEffect(() => clearMarks(), [source]);
  const sourceKind = source?.kind, where = sourceWhere(source);
  useEffect(() => {
    if (sourceKind === "search") return;
    stopAsking();
  }, [sourceKind, where]);
  const accountsNow = accounts.join();   // by value: the array itself is new each render
  const look = useEffectEvent(() => { if (!document.hidden) lookedAt(source, accounts); });
  useEffect(() => {
    const onVisibility = () => {
      if (!document.hidden) cameBack();
      look();
    };
    look();
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [source, accountsNow]);
  useEffect(() => document.body.style.setProperty("--measure", `${WIDTHS[prefs.width]}px`), [prefs.width]);

  useSwipe(
    (copy) => m.file(all.find((r) => r.id === copyId(copy)) ?? copy, "archive"),
    (copy) => m.snooze(copy, { preset: "tomorrow" }, [copyId(copy)]),
  );
  const ui = { toggleRail, openSearchBar, saveSearch: () => void saveSearch(m), createFilter: () => createFilter(m) };
  // The guide opens on the page it points at.
  const showGuide = () => {
    setReadFocus(false);
    if (opened || !inInbox || searching) go({ source: firstAccount ? { kind: "folder", account: firstAccount, folder: "INBOX" } : { kind: "allInboxes" }, opened: null });
    guideOpen.set(true);
  };
  const commands = buildCommands(m, ui);
  useKeys(m, commands);

  const pages = list.data?.pages ?? [];
  const last = pages[pages.length - 1];
  const more = list.hasNextPage ? "+" : "";
  const noun = m.grouped && !searching && m.source?.kind !== "digest" ? "conversation" : "message";
  const lane = source?.kind === "search" ? source.lane : undefined;
  const screening = folder === "__screener__";
  const documents = source?.kind === "documents";
  const screener = useScreener(screening ? m.account : undefined);
  const toScreen = screener.data?.total ?? 0;
  const laneNoun = lane === "feed" ? "newsletter" : "receipt";
  const count = screening ? `${toScreen} to screen` : lane
    ? `${all.length}${more} ${laneNoun}${all.length === 1 && !more ? "" : "s"}`
    : searching
    ? `${all.length}${more} result${all.length === 1 && !more ? "" : "s"}`
    : `${all.length}${more} ${noun}${all.length === 1 && !more ? "" : "s"}${last?.syncing || last?.catchingUp ? " · reading the rest…" : ""}`;

  let empty: React.ReactNode = <div className="nores">{searching ? "No matches." : m.source?.kind === "digest" ? "Nothing left here." : "Nothing here."}</div>;
  if (m.digestFailed) empty = <div className="nores">Couldn’t reach the server. <button className="btn-retry" id="dgRetry" onClick={m.retryDigest}>Retry</button></div>;
  else if (m.digest && !accounts.length) empty = <div className="nores firstrun"><div className="fr-h">Welcome to MailMind</div><div className="fr-b">No email accounts connected yet.</div><button className="abtn prim" id="frAdd" onClick={m.openSettings}>+ Connect your first account</button></div>;
  else if (list.isError) empty = <div className="nores" role="status">{searching ? "Search failed." : "Couldn’t load messages."} <button className="btn-retry" onClick={() => void list.refetch()}>Retry</button></div>;
  else if (list.isPending && source) empty = <div className="nores" role="status">Loading…</div>;
  else if (last?.error) empty = <div className="nores">{last.error}</div>;
  else if (inInbox && !searching && tab !== "all" && all.length) empty = <div className="nores">{tab === "needs" ? "Nothing needs you." : "Nothing unread."} Everything else is in <button className="linkbtn" onClick={() => setInboxTab("all")}>Everything</button>.</div>;
  else if (last?.syncing) empty = <FirstRead />;
  else if (last?.syncFailed) empty = <div className="nores" role="status">Couldn’t sync this folder. <button className="btn-retry" onClick={() => void list.refetch()}>Retry</button></div>;

  const footer = list.hasNextPage ? <button className="load-more" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>{searching ? "Load more results" : "Load 200 more"}</button> : null;
  const counts: Record<InboxTab, string> = {
    needs: String(all.filter((r) => r.needsYou).length),
    all: `${all.length}${more}`,
    unread: String(all.filter((r) => !r.seen).length),
  };
  return (
    <>
      <TopBar m={m} readOnly={isReadOnly} />
      <AccountAlert m={m} />
      <main className="split">
        <FolderRail accounts={accounts} aliases={aliases} source={source} down={badAccounts(m.digest)} />
        <Splitter />
        <section className="list" id="list">
          <div className="listhead">
            <div className="lh1">
              <h1 className="lh-title" id="listTitle">{title}</h1>
              {!searching && !screening && source?.kind !== "digest" && !documents && (
                <button className={m.grouped ? "thread-toggle on" : "thread-toggle"} id="threadToggle" title="Group into conversations" onClick={() => setGrouping(!m.grouped)}>
                  <Icon name={m.grouped ? "chat" : "list"} /> {m.grouped ? "Conversations" : "Messages"}
                </button>
              )}
            </div>
            {lane && (
              <div className="client-ctx" id="clientCtx">
                <Icon name={lane === "feed" ? "book" : "receipt"} /> <b>{title}</b> · {lane === "feed" ? "a calm reading river" : "receipts & invoices"} · <ScopeToggle account={source?.kind === "search" ? source.account : ""} />
              </div>
            )}
            {screening && <div className="client-ctx" id="clientCtx"><b>Screener</b> · {toScreen} new sender{toScreen === 1 ? "" : "s"} awaiting a decision</div>}
            {searching && !lane && source?.kind === "search" && (
              <div className="client-ctx" id="clientCtx">
                <Icon name="search" /> <b>{source.q}</b> · {all.length} {!source.thread && <ScopeToggle account={source.account} />}<ConversationScope m={m} source={source} /> <TranslatedChip source={source} />
                {!onSavedView(m) && <> · <button className="ctx-save" title="Save as a view (v)" onClick={() => void saveSearch(m)}>+ Save view</button></>}
                {!source.thread && <> · <button className="ctx-filter" id="createFilter" title="Star or mark read new mail that fits this search" onClick={() => createFilter(m)}>+ Create filter</button></>}
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
            {!documents && <div className="lh-row"><span id="count">{asked ? (asked.state === "loading" ? "asking…" : asked.state === "answer" ? `${asked.answer.sources?.length ?? 0} source${asked.answer.sources?.length === 1 ? "" : "s"}` : "") : (screening ? screener.isSuccess : list.isSuccess) ? count : "—"}</span></div>}
          </div>
          <BulkBar m={m} />
          <NewMailPill />
          {asked ? <AskPane at={asked} m={m} /> : documents ? <Hub m={m} /> : screening ? <ScreenerPane account={m.account} /> : lane === "feed" ? <FeedRiver m={m} footer={footer} /> : lane === "papertrail" ? <PaperTrail m={m} footer={footer} /> : <MessageList
            above={inInbox && !searching ? <BriefRow m={m} /> : null}
            rows={rows}
            openId={m.openId}
            marks={m.marks}
            label={title}
            folder={folder}
            isWhole={m.isWhole}
            accountChip={(r) => (source?.kind === "folder" ? null : aliases[r.account] || shortAddress(r.account))}
            onOpen={m.open}
            onFile={m.file}
            onStar={m.starRow}
            onSeen={m.seenRow}
            onSnooze={(r, anchor) => snoozeAsk.set({ target: r.count > 1 && r.threadId ? { account: r.account, thread_id: r.threadId } : copyOf(r), rows: [r.id], anchor, canUnsnooze: false })}
            onFocusRead={() => setReadFocus(true)}
            empty={empty}
            footer={footer}
          />}
        </section>
        <Reader m={m} />
      </main>
      <RailScrim />
      <SnoozeMenu onSnooze={m.snooze} onUnsnooze={m.unsnooze} />
      <Palette commands={commands} />
      <MoveTo m={m} />
      <ComposerModal
        senders={m.digest?.send_accounts?.length ? m.digest.send_accounts : accounts}
        aliases={aliases}
        ai={m.digest?.ai?.engine !== "none"}
      />
      <Panels m={m} />
      <DoPanel account={source?.kind === "folder" || source?.kind === "search" ? source.account : ""} />
      <Help legend={() => legendOf(m, ui)} onGuide={showGuide} />
      <Guide m={m} />
      <Settings />
      <Curate />
      <OverLimitDialog />
      <ConfirmDialog />
      <Floats />
      <Toaster />
    </>
  );
}
