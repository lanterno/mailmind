// The message list, virtualized: one fixed row height, a node only for the rows on screen, and only the rows the
// core says to hold kept in memory. Rows the core has not sent yet show as placeholders.
import { useCallback, useEffect, useLayoutEffect, useReducer, useRef, useState } from "react";
import type { CoreClient } from "../core/client";
import { describe } from "../core/client";
import type { ListQuery } from "../core/generated/ListQuery";
import type { MessageRow } from "../core/generated/MessageRow";
import { ListRows } from "../core/list-rows";

const ROW_HEIGHT = 64;
// Rows drawn past each edge of the screen, so a fast scroll shows rows rather than gaps.
const OVERSCAN = 6;

type Opened = { view: number; total: number };

export function MessageList({ client, query }: { client: CoreClient; query: ListQuery }) {
  const [rows] = useState(() => new ListRows());
  const [opened, setOpened] = useState<Opened>();
  const [failure, setFailure] = useState<string>();
  const [, rowsArrived] = useReducer((n: number) => n + 1, 0);
  const [visible, setShown] = useState({ first: 0, count: 0 });
  const scroller = useRef<HTMLDivElement>(null);
  const frame = useRef(0);

  useEffect(() => {
    let view: number | undefined;
    let closed = false;
    client
      .openList(query, {
        batch(batch) {
          rows.apply(batch);
          rowsArrived();
        },
        refused: (refusal) => setFailure(describe(refusal)),
      })
      .then(
        (list) => {
          if (closed) {
            void client.closeView(list.view);
            return;
          }
          view = list.view;
          rows.total = list.total;
          setOpened(list);
        },
        (e: unknown) => setFailure(e instanceof Error ? e.message : String(e)),
      );
    return () => {
      closed = true;
      if (view !== undefined) void client.closeView(view);
    };
  }, [client, query, rows]);

  const measure = useCallback(() => {
    frame.current = 0;
    const element = scroller.current;
    if (!element) return;
    const first = Math.floor(element.scrollTop / ROW_HEIGHT);
    const count = Math.ceil(element.clientHeight / ROW_HEIGHT) + 1;
    setShown((was) => (was.first === first && was.count === count ? was : { first, count }));
  }, []);

  // Scroll events come faster than frames: the window is read once a frame, and sent only when it changed.
  const onScroll = useCallback(() => {
    if (!frame.current) frame.current = requestAnimationFrame(measure);
  }, [measure]);

  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element) return;
    measure();
    const resized = new ResizeObserver(measure);
    resized.observe(element);
    return () => {
      resized.disconnect();
      cancelAnimationFrame(frame.current);
    };
  }, [measure]);

  useEffect(() => {
    if (opened && visible.count > 0) {
      client.setVisible(opened.view, visible.first, visible.count).catch((e: unknown) => setFailure(String(e)));
    }
  }, [client, opened, visible]);

  const firstShown = rows.row(visible.first) !== undefined;
  useFirstRowsMark(firstShown);

  const total = opened?.total ?? 0;
  const start = Math.max(0, visible.first - OVERSCAN);
  const end = Math.min(total, visible.first + visible.count + OVERSCAN);
  const drawn = [];
  for (let i = start; i < end; i++) {
    drawn.push(<Row key={i} index={i} total={total} row={rows.row(i)} />);
  }

  return (
    <div className="list" ref={scroller} onScroll={onScroll} role="list" aria-label="Inbox" aria-busy={!firstShown}>
      {failure && <p className="failure">{failure}</p>}
      <div className="list-body" style={{ height: total * ROW_HEIGHT }}>
        {drawn}
      </div>
    </div>
  );
}

function Row({ index, total, row }: { index: number; total: number; row: MessageRow | undefined }) {
  const place = { transform: `translateY(${index * ROW_HEIGHT}px)` };
  if (!row) {
    return (
      <div className="row placeholder" style={place} role="listitem" aria-posinset={index + 1} aria-setsize={total}>
        <span className="bar-short" />
        <span className="bar-long" />
      </div>
    );
  }
  return (
    <div className={row.unread ? "row unread" : "row"} style={place} role="listitem" aria-posinset={index + 1} aria-setsize={total}>
      <div className="line">
        <span className="sender">{row.sender}</span>
        {row.flagged && <span className="mark flagged" aria-label="Flagged">⚑</span>}
        {row.attachment && <span className="mark" aria-label="Has an attachment">📎</span>}
        <time className="date" dateTime={new Date(row.date * 1000).toISOString()}>
          {row.date ? DATE.format(row.date * 1000) : ""}
        </time>
      </div>
      <div className="line">
        <span className="subject">{row.subject}</span>
        <span className="snippet">{row.snippet}</span>
      </div>
    </div>
  );
}

const DATE = new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" });

// Marks when the first rows are in the page, for `just web measure` and `just desktop measure`:
// "mailmind:rows-committed" and the window's title (which the desktop shell can see) once React has put them in
// the DOM, and "mailmind:first-rows" once a frame showing them has painted. A hidden window paints no frames, so
// only the first two come there.
function useFirstRowsMark(shown: boolean): void {
  const marked = useRef(false);
  useEffect(() => {
    if (!shown || marked.current) return;
    marked.current = true;
    performance.mark("mailmind:rows-committed");
    document.title = "Inbox · MailMind";
    // The frame after this commit has painted once the next task runs.
    requestAnimationFrame(() => setTimeout(() => performance.mark("mailmind:first-rows")));
  }, [shown]);
}
