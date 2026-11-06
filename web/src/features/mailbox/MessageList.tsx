// The list: a real listbox. Focus stays on the box and aria-activedescendant says which row you're on, so j and k move a
// screen reader's place too. A row's own actions are for the mouse: an option can't hold a control, and the keyboard has
// e and d.
import { useEffect, useRef } from "react";
import { avatarHue, dayGroup, initialsOf } from "../../lib/text";
import { Icon } from "../../lib/ui/Icon";
import { toggleMark, type Filing } from "./actions";
import { inDaySections, type Row } from "./rows";

const rowId = (pk: number) => `row-${pk}`;

function RowItem({ row, whole, selected, isMarked, showAccount, onOpen, onFile }: {
  row: Row;
  whole: boolean;
  selected: boolean;
  isMarked: boolean;
  showAccount: string | null;
  onOpen: () => void;
  onFile: (kind: Filing) => void;
}) {
  const needs = Boolean(row.action);
  const what = whole ? " conversation" : "";
  const className = `row cmsg${row.seen ? "" : " unread"}${needs ? " needs" : ""}${selected ? " sel" : ""}${isMarked ? " marked" : ""}`;
  const act = (kind: Filing) => (e: React.MouseEvent) => {
    e.stopPropagation();
    onFile(kind);
  };
  return (
    <div id={rowId(row.pk)} role="option" aria-selected={selected} aria-checked={isMarked} className={className} data-pk={row.pk} data-tid={row.threadId || undefined} onClick={onOpen}>
      <div className="avwrap"><span className="av" style={{ "--h": avatarHue(row.fromAddr || row.fromName) } as React.CSSProperties} aria-hidden="true">{initialsOf(row.fromName, row.fromAddr)}</span>
        <span className={isMarked ? "cbx on" : "cbx"} aria-hidden="true" title="Select (x)" onClick={(e) => { e.stopPropagation(); toggleMark(row.pk); }}>{isMarked ? "✓" : ""}</span>
      </div>
      <div className="cmsg-body">
        <div className="r1">
          <span className="from">{row.fromName}</span>
          {row.count > 1 && <span className="thr-count" title={`${row.count} messages`}>{row.count}</span>}
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
        <span className="ract" title={`Archive${what} (e)`} onClick={act("archive")}><Icon name="archive" /></span>
        <span className="ract del" title={`Delete${what} (d)`} onClick={act("trash")}><Icon name="trash" /></span>
      </div>
    </div>
  );
}

export function MessageList({ above, rows, openPk, marks, label, folder, isWhole, accountChip, onOpen, onFile, empty, footer }: {
  above?: React.ReactNode;
  rows: Row[];
  openPk: number | null;
  marks: ReadonlySet<number>;
  label: string;
  folder: string;
  isWhole: (r: Row) => boolean;
  accountChip: (r: Row) => string | null;
  onOpen: (r: Row) => void;
  onFile: (r: Row, kind: Filing) => void;
  empty: React.ReactNode;
  footer: React.ReactNode;
}) {
  const sections = inDaySections(rows, folder);
  const scrolled = useRef<number | null>(null);
  useEffect(() => {
    if (openPk === null || scrolled.current === openPk) return;
    scrolled.current = openPk;
    document.getElementById(rowId(openPk))?.scrollIntoView({ block: "nearest" });
  }, [openPk, rows.length]);
  if (!rows.length) return <div id="rows">{above}{empty}</div>;
  let section = "";
  const active = openPk !== null && rows.some((r) => r.pk === openPk) ? rowId(openPk) : undefined;
  return (
    <div id="rows">
      {above}
      <div className="rows-box" role="listbox" tabIndex={0} aria-label={label} aria-activedescendant={active}>
        {rows.flatMap((row) => {
          const here = sections && row.ts !== null ? dayGroup(row.ts) : "";
          const heading = here && here !== section ? <div key={`g-${here}`} className="rgrp" role="presentation">{here}</div> : null;
          if (here) section = here;
          const item = (
            <RowItem key={row.pk} row={row} whole={isWhole(row)} selected={row.pk === openPk} isMarked={marks.has(row.pk)} showAccount={accountChip(row)} onOpen={() => onOpen(row)} onFile={(kind) => onFile(row, kind)} />
          );
          return heading ? [heading, item] : [item];
        })}
      </div>
      {footer}
    </div>
  );
}
