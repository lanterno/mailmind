// The list: a real listbox. Focus stays on the box and aria-activedescendant says which row you're on, so j and k move a
// screen reader's place too. A row's own actions are for the mouse: an option can't hold a control, and the keyboard has
// e, d and h.
import { useEffect, useRef } from "react";
import { minute } from "../../lib/clock";
import { avatarHue, dayGroup, initialsOf } from "../../lib/text";
import { Icon } from "../../lib/ui/Icon";
import { toggleMark, type Filing } from "./actions";
import { inDaySections, type Row } from "./rows";

// A row's DOM id, from its copy's (`Row.id`): a folder's name can hold a space, which an id can't.
const rowId = (id: string) => `row-${encodeURIComponent(id)}`;

function RowItem({ row, whole, selected, isMarked, showAccount, onOpen, onFile, onSnooze, onFocusRead }: {
  row: Row;
  whole: boolean;
  selected: boolean;
  isMarked: boolean;
  showAccount: string | null;
  onOpen: () => void;
  onFile: (kind: Filing) => void;
  onSnooze: (anchor: Element) => void;
  onFocusRead: () => void;
}) {
  const needs = Boolean(row.action);
  const what = whole ? " conversation" : "";
  const className = `row cmsg${row.seen ? "" : " unread"}${needs ? " needs" : ""}${selected ? " sel" : ""}${isMarked ? " marked" : ""}`;
  const act = (kind: Filing) => (e: React.MouseEvent) => {
    e.stopPropagation();
    onFile(kind);
  };
  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/interactive-supports-focus -- focus stays on the listbox, which names this through aria-activedescendant, and the app's keys act on it
    <div id={rowId(row.id)} role="option" aria-selected={selected} aria-checked={isMarked} className={className} data-key={row.key} data-folder={row.folder} data-uid={row.uid} data-tid={row.threadId || undefined} onClick={onOpen} onDoubleClick={(e) => { if (!(e.target as Element).closest(".cbx,.racts")) onFocusRead(); }}>
      <div className="avwrap"><span className="av" style={{ "--h": avatarHue(row.fromAddr || row.fromName) } as React.CSSProperties} aria-hidden="true">{initialsOf(row.fromName, row.fromAddr)}</span>
        <span className={isMarked ? "cbx on" : "cbx"} aria-hidden="true" title="Select (x)" onClick={(e) => { e.stopPropagation(); toggleMark(row.id); }}>{isMarked ? "✓" : ""}</span>
      </div>
      <div className="cmsg-body">
        <div className="r1">
          <span className="from">{row.fromName}</span>
          {row.count > 1 && row.threadCount > 1 && <span className="thr-count" title={`${row.threadCount} messages`}>{row.threadCount}</span>}
          {row.flagged && <span className="rflag" style={{ color: "var(--gold)" }}><Icon name="star" className="fill" /></span>}
          {row.hasAttach && <span className="rflag" style={{ color: "var(--mute)" }}><Icon name="clip" /></span>}
          {row.isJunk && <span className="tags"><span className="jb">{(row.bucket || "junk").split("/")[0]}</span></span>}
          {showAccount && <span className="acctchip">{showAccount}</span>}
          <span className="when">{row.when}</span>
        </div>
        <div className="r2sub">{!row.seen && <span className="visually-hidden">Unread. </span>}<span className="sj">{row.subject}</span></div>
        {row.summary
          ? <div className="gist"><span className="visually-hidden">Summary: </span>{row.summary}</div>
          : <div className="pv">{row.snippet}</div>}
        {needs && <div className="r4"><span className="rchip need" title={row.action}><span className="visually-hidden">Next step: </span>{row.action}</span></div>}
      </div>
      <div className="racts" aria-hidden="true">
        {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- the pointer's shortcut, hidden from assistive tech with its parent: the e key is the keyboard's */}
        <span className="ract" title={`Archive${what} (e)`} onClick={act("archive")}><Icon name="archive" /></span>
        {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- the pointer's shortcut, hidden from assistive tech with its parent: the d key is the keyboard's */}
        <span className="ract del" title={`Delete${what} (d)`} onClick={act("trash")}><Icon name="trash" /></span>
        {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions -- the pointer's shortcut, hidden from assistive tech with its parent: the h key is the keyboard's */}
        <span className="ract" data-racts="snooze" title="Snooze (h)" onClick={(e) => { e.stopPropagation(); onSnooze(e.currentTarget); }}><Icon name="clock" /></span>
      </div>
    </div>
  );
}

export function MessageList({ above, rows, openId, marks, label, folder, isWhole, accountChip, onOpen, onFile, onSnooze, onFocusRead, empty, footer }: {
  above?: React.ReactNode;
  rows: Row[];
  openId: string | null;
  marks: ReadonlySet<string>;
  label: string;
  folder: string;
  isWhole: (r: Row) => boolean;
  accountChip: (r: Row) => string | null;
  onOpen: (r: Row) => void;
  onFile: (r: Row, kind: Filing) => void;
  onSnooze: (r: Row, anchor: Element) => void;
  onFocusRead: () => void;
  empty: React.ReactNode;
  footer: React.ReactNode;
}) {
  const now = minute.useValue();   // compiled, this list keeps its headings until an input changes: midnight is one
  const sections = inDaySections(rows, folder);
  const scrolled = useRef<string | null>(null);
  useEffect(() => {
    if (openId === null || scrolled.current === openId) return;
    scrolled.current = openId;
    document.getElementById(rowId(openId))?.scrollIntoView({ block: "nearest" });
  }, [openId, rows.length]);
  if (!rows.length) return <div id="rows">{above}{empty}</div>;
  const active = openId !== null && rows.some((r) => r.id === openId) ? rowId(openId) : undefined;
  const items: React.ReactNode[] = [];
  let section = "";
  for (const row of rows) {
    const here = sections && row.ts !== null ? dayGroup(row.ts, new Date(now)) : "";
    if (here && here !== section) items.push(<div key={`g-${here}`} className="rgrp" role="presentation">{here}</div>);
    if (here) section = here;
    items.push(<RowItem key={row.id} row={row} whole={isWhole(row)} selected={row.id === openId} isMarked={marks.has(row.id)} showAccount={accountChip(row)} onOpen={() => onOpen(row)} onFile={(kind) => onFile(row, kind)} onSnooze={(anchor) => onSnooze(row, anchor)} onFocusRead={onFocusRead} />);
  }
  return (
    <div id="rows">
      {above}
      <div className="rows-box" role="listbox" tabIndex={0} aria-label={label} aria-activedescendant={active}>
        {items}
      </div>
      {footer}
    </div>
  );
}
