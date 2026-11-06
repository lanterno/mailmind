// The app's one view: the rail, the list and the reader, and the keys that work across them.
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { fileRow, filing, readOnly, undo, undoFloat, markUnread, setStar, type Filing } from "../features/mailbox/actions";
import { groupedPref, saveGroupedPref, useDigest, useFolders, useMessages } from "../features/mailbox/data";
import { FolderRail } from "../features/mailbox/FolderRail";
import { MessageList } from "../features/mailbox/MessageList";
import { Reader } from "../features/mailbox/Reader";
import { folderTitle, inTab, isConversation, type InboxTab, type Row } from "../features/mailbox/rows";
import { shortAddress } from "../lib/text";
import { store, stored } from "../lib/store";
import { Toaster } from "../lib/toast";
import { Icon } from "../lib/ui/Icon";
import { newBuild, useRealtime } from "./realtime";
import { go, useRoute, type Opened, type Route } from "./route";
import { Splitter } from "./Splitter";
import { closeRailDrawer, RailScrim, TopBar, toggleRail } from "./TopBar";

const TABS: [InboxTab, string][] = [["needs", "Needs you"], ["all", "Everything"], ["unread", "Unread"]];

function initialTab(): InboxTab {
  const saved = stored("mm_inbox_tab");
  return saved === "all" || saved === "unread" ? saved : "needs";
}

function Floats() {
  const qc = useQueryClient();
  const label = undoFloat.useValue();
  const build = newBuild.useValue();
  return (
    <>
      <div className="send-float undo-float" id="undoFloat" hidden={!label}>
        <span>{label}</span><button id="ufUndo" onClick={() => void undo(qc)}>Undo · <kbd>z</kbd></button>
      </div>
      <div className="send-float build-float" id="buildFloat" role="status" hidden={!build}>
        MailMind was updated. <button id="buildReload" onClick={() => location.reload()}>Reload</button>
      </div>
    </>
  );
}

export function App() {
  useRealtime();
  const qc = useQueryClient();
  const route = useRoute();
  const { source, opened } = route;
  const digest = useDigest();
  const accounts = digest.data?.accounts ?? [];
  const aliases = digest.data?.aliases ?? {};
  useEffect(() => readOnly.set(digest.data?.profile === "read-only"), [digest.data?.profile]);

  // /next on its own opens the first account's Inbox.
  useEffect(() => {
    if (!source && accounts[0]) go({ source: { kind: "folder", account: accounts[0], folder: "INBOX" }, opened: null }, { replace: true });
  }, [source, accounts[0]]);

  const [grouped, setGrouped] = useState(groupedPref);
  const [tab, setTab] = useState(initialTab);
  const list = useMessages(source, grouped, accounts);
  const hidden = filing.useValue();
  const folderName = source?.kind === "folder" ? source.folder : source?.kind === "allInboxes" ? "INBOX" : "";
  const account = source?.kind === "folder" ? source.account : undefined;
  const { data: folderData } = useFolders(account);
  const inInbox = source?.kind === "allInboxes" || folderName.toUpperCase() === "INBOX";
  const searching = source?.kind === "search";
  const pages = list.data?.pages ?? [];
  const all = pages.flatMap((p) => p.rows).filter((r) => !hidden.has(r.pk));
  const openPk = opened?.pk ?? null;
  const rows = inInbox ? inTab(all, tab, openPk) : all;
  const last = pages[pages.length - 1];
  const more = list.hasNextPage ? "+" : "";
  const noun = grouped && !searching ? "conversation" : "message";
  const isWhole = (r: Row) => !searching && isConversation(r, grouped, r.folder);
  const listRef = useRef<HTMLDivElement>(null);

  const reading = Boolean(opened);
  useEffect(() => void document.body.classList.toggle("reading", reading), [reading]);
  useEffect(closeRailDrawer, [source]);

  const routeFor = (r: Row): Opened =>
    isWhole(r) ? { kind: "thread", id: r.threadId, pk: r.pk, account: r.account } : { kind: "message", pk: r.pk };
  const openRow = (r: Row) => go({ source, opened: routeFor(r) }, { replace: Boolean(opened) });

  const afterFiling = (r: Pick<Row, "pk">): Route | null => {
    if (opened?.pk !== r.pk) return null;
    const i = rows.findIndex((x) => x.pk === r.pk);
    const next = rows[i + 1] ?? rows[i - 1];
    return { source, opened: next && i >= 0 ? routeFor(next) : null };
  };
  const file = (r: Pick<Row, "pk">, kind: Filing) => {
    const whole = opened?.kind === "thread" && opened.pk === r.pk ? true : "threadId" in r ? isWhole(r as Row) : false;
    void fileRow(qc, kind, r, whole, afterFiling(r));
  };
  const openRowData = openPk === null ? undefined : all.find((r) => r.pk === openPk);
  const move = (by: number) => {
    const i = rows.findIndex((r) => r.pk === openPk);
    const target = rows[i < 0 ? 0 : Math.max(0, Math.min(rows.length - 1, i + by))];
    if (target) openRow(target);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const at = e.target as Element | null;
      if (at?.closest?.('input,textarea,select,[contenteditable="true"],[role="menu"],[role="dialog"]')) return;
      const acted = (fn: () => void) => {
        e.preventDefault();
        fn();
      };
      const target = openPk === null ? undefined : { pk: openPk };
      switch (e.key) {
        case "j": case "ArrowDown": return acted(() => move(1));
        case "k": case "ArrowUp": return acted(() => move(-1));
        case "/": return acted(() => document.querySelector<HTMLInputElement>("#search")?.focus());
        case "[": return acted(toggleRail);
        case "z": return acted(() => void undo(qc));
        case "Escape": return opened ? acted(() => go({ source, opened: null })) : undefined;
        case "e": return target && acted(() => file(openRowData ?? target, "archive"));
        case "d": case "#": case "Delete": case "Backspace": return target && acted(() => file(openRowData ?? target, "trash"));
        case "b": return target && acted(() => file(openRowData ?? target, "junk"));
        case "s": return openRowData && acted(() => void setStar(qc, openRowData));
        case "u": return target && acted(() => void markUnread(qc, target.pk));
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

  const title = source?.kind === "allInboxes" ? "All inboxes" : searching ? "Search" : folderTitle(folderName, folderData?.folders);
  const count = `${all.length}${more} ${noun}${all.length === 1 && !more ? "" : "s"}${last?.syncing || last?.catchingUp ? " · reading the rest…" : ""}`;

  let empty: React.ReactNode = <div className="nores">Nothing here.</div>;
  if (digest.isSuccess && !accounts.length) empty = <div className="nores firstrun"><div className="fr-h">Welcome to MailMind</div><div className="fr-b">No email accounts connected yet. Connect one in the <a href="/">current app</a>.</div></div>;
  else if (list.isError) empty = <div className="nores" role="status">Couldn’t load this list. <button className="ai-rerun" onClick={() => void list.refetch()}>Retry</button></div>;
  else if (list.isPending && source) empty = <div className="nores" role="status">Loading…</div>;
  else if (last?.error) empty = <div className="nores">{last.error}</div>;
  else if (inInbox && tab !== "all" && all.length) empty = <div className="nores">{tab === "needs" ? "Nothing needs you." : "Nothing unread."} Everything else is in <button className="linkbtn" onClick={() => { setTab("all"); store("mm_inbox_tab", "all"); }}>Everything</button>.</div>;
  else if (last?.syncing) empty = <div className="nores" role="status">Reading this folder for the first time…</div>;
  else if (last?.syncFailed) empty = <div className="nores" role="status">Couldn’t sync this folder. <button className="ai-rerun" onClick={() => void list.refetch()}>Retry</button></div>;

  const counts: Record<InboxTab, string> = {
    needs: String(all.filter((r) => r.needsYou).length),
    all: `${all.length}${more}`,
    unread: String(all.filter((r) => !r.seen).length),
  };
  return (
    <>
      <TopBar source={source} />
      <main className="split">
        <FolderRail accounts={accounts} aliases={aliases} source={source} />
        <Splitter />
        <section className="list" id="list">
          <div className="listhead">
            <div className="lh1">
              <h1 className="lh-title" id="listTitle">{title}</h1>
              {!searching && (
                <button
                  className={grouped ? "thread-toggle on" : "thread-toggle"}
                  id="threadToggle"
                  title="Group into conversations"
                  onClick={() => {
                    saveGroupedPref(!grouped);
                    setGrouped(!grouped);
                  }}
                >
                  <Icon name={grouped ? "chat" : "list"} /> {grouped ? "Conversations" : "Messages"}
                </button>
              )}
            </div>
            {inInbox && !searching && (
              <div className="lh-tabs" id="lhTabs" role="tablist" aria-label="Inbox">
                {TABS.map(([id, label]) => (
                  <button
                    key={id}
                    className="lh-tab"
                    role="tab"
                    id={`tab-${id}`}
                    aria-selected={tab === id}
                    aria-controls="rows"
                    tabIndex={tab === id ? 0 : -1}
                    onClick={() => {
                      setTab(id);
                      store("mm_inbox_tab", id);
                    }}
                  >
                    {label}<span className={id === "needs" && counts.needs !== "0" ? "n sun" : "n"}>{counts[id]}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="lh-row"><span id="count">{list.isSuccess ? count : "—"}</span></div>
          </div>
          <MessageList
            rows={rows}
            openPk={openPk}
            label={title}
            folder={folderName}
            isWhole={isWhole}
            accountChip={(r) => (source?.kind === "folder" ? null : aliases[r.account] || shortAddress(r.account))}
            onOpen={openRow}
            onFile={(r, kind) => file(r, kind)}
            listRef={listRef}
            empty={empty}
            footer={list.hasNextPage ? <button className="load-more" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>{searching ? "Load more results" : "Load 200 more"}</button> : null}
          />
        </section>
        <Reader
          opened={opened}
          row={openRowData}
          folderTag={title}
          onFile={(kind) => {
            if (opened) file(openRowData ?? { pk: opened.pk }, kind);
          }}
          onBack={() => go({ source, opened: null })}
        />
      </main>
      <RailScrim />
      <Floats />
      <Toaster />
    </>
  );
}
