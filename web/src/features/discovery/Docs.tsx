// One findable place for every attachment across accounts, at /documents. The server filters and pages, so a search reaches
// every file and not only those loaded.
import { Fragment, useEffect, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { EMPTY } from "../../lib/emptyWords";
import { get } from "../../lib/api/client";
import type { DocumentGrouping, DocumentKind } from "../../lib/api/types";
import { entries } from "../../lib/entries";
import { copyId, copyOf } from "../../lib/copy";
import { fileSize } from "../../lib/text";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import type { Mailbox } from "../mailbox";

const PAGE = 200;
// The server's kinds of document: each one's icon, and its words in the order the facets show them.
const ICON: Record<DocumentKind, string> = { pdf: "file", image: "image", doc: "file", sheet: "table", slide: "slides", archive: "archive", text: "file", other: "clip" };
const LABEL: Record<DocumentKind, string> = { pdf: "PDFs", image: "Images", doc: "Docs", sheet: "Sheets", slide: "Slides", archive: "Archives", text: "Text", other: "Other" };
// The sections the server can put them in, and the plain list. Sections by sender come first, the newest sender at the top, so the newest file still is.
const GROUPED: Record<DocumentGrouping | "newest", string> = { sender: "By sender", type: "By type", newest: "Newest" };

export function Hub({ m }: { m: Mailbox }) {
  const [kind, setKind] = useState<DocumentKind | "all">("all");
  const [typed, setTyped] = useState("");
  const [q, setQ] = useState("");
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [group, setGroup] = useState<DocumentGrouping | "newest">("sender");
  useEffect(() => {
    const t = setTimeout(() => setQ(typed.trim()), 120);
    return () => clearTimeout(t);
  }, [typed]);
  const list = useInfiniteQuery({
    queryKey: ["documents", kind, q, group],
    initialPageParam: 0,
    gcTime: 0,
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams({ limit: String(PAGE), offset: String(pageParam) });
      if (kind !== "all") p.set("kind", kind);
      if (q) p.set("q", q);
      if (group !== "newest") p.set("group", group);
      return get(`/api/documents?${p}`);
    },
    // How far through the server's list, not how many rows are held: new mail moves the rest along.
    getNextPageParam: (last, pages) => {
      const read = pages.reduce((n, page) => n + (page.docs?.length ?? 0), 0);
      return read < (last.matched ?? 0) && last.docs?.length ? read : undefined;
    },
  });
  useEffect(() => {
    if (list.isFetchNextPageError) toast("Couldn’t load more files.");
  }, [list.isFetchNextPageError]);
  // Keep your place: the focus goes to the first new file, not back to the top of the dialog.
  const more = async (held: number) => {
    await list.fetchNextPage();
    requestAnimationFrame(() => document.querySelectorAll<HTMLElement>("#docList .doc-dl")[held]?.focus());
  };
  const pages = list.data?.pages ?? [];
  const first = pages[0];
  const seen = new Set<string>();
  const docs = pages.flatMap((p) => p.docs ?? []).filter((x) => {
    const id = `${copyId(x)}/${x.part_id}/${x.filename}`;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
  const read = pages.reduce((n, p) => n + (p.docs?.length ?? 0), 0);
  const matched = first?.matched ?? 0, total = first?.total ?? 0, left = matched - read;
  const facets = (first?.facets ?? {}) as unknown as Record<string, number>;
  const sections = new Map((first?.groups ?? []).map((g) => [g.id, g]));
  const called = (x: (typeof docs)[number]) => (group === "type" ? LABEL[x.kind] : sections.get(x.group)?.name || "No sender");
  let body: React.ReactNode;
  if (list.isError && !first) body = <div className="doc-empty">Couldn’t load documents.</div>;
  else if (!first) body = <div className="doc-empty" role="status"><span className="spin">↻</span> Gathering your files…</div>;
  else if (!docs.length) body = <div className="doc-empty">{total ? EMPTY.noFile : EMPTY.noDocuments}</div>;
  else {
    body = (
      <>
        {docs.map((x, i) => {
          const id = `${copyId(x)}/${x.part_id}/${x.filename}`, showing = previewing === id;
          return (
            <Fragment key={id}>
              {group !== "newest" && x.group !== docs[i - 1]?.group && <h2 className="doc-sec">{called(x)} <span className="c">{sections.get(x.group)?.count}</span></h2>}
              <div className="doc-row" data-doc-key={x.key}>
                <div className="doc-ic"><Icon name={ICON[x.kind]} /></div>
                <button className="doc-body" onClick={() => m.openAny(copyOf(x))}>
                  <div className="doc-fn">{x.filename}</div>
                  <div className="doc-meta"><span className="who">{x.from}</span> · {x.subject} · {x.when}</div>
                </button>
                <span className="doc-sz">{fileSize(x.size)}</span>
                {x.preview && <button className="doc-dl" title="Preview" aria-label={`Preview ${x.filename}`} aria-expanded={showing} onClick={() => setPreviewing(showing ? null : id)}><Icon name="eye" /></button>}
                <a className="doc-dl" href={x.url} download={x.filename} title="Download" aria-label={`Download ${x.filename}`}><Icon name="download" /></a>
              </div>
              {x.preview && showing && <iframe className="doc-preview" sandbox="" src={x.preview} title={`Preview of ${x.filename}`} />}
            </Fragment>
          );
        })}
        {left > 0 && <button className="doc-more" disabled={list.isFetchingNextPage} onClick={() => void more(docs.length)}>{list.isFetchingNextPage ? "Loading…" : `Show more · ${left} left`}</button>}
      </>
    );
  }
  return (
    <div className="docs-lane">
      <input className="doc-search" id="docSearch" placeholder="Search files, senders, subjects…" aria-label="Search documents" value={typed} onChange={(e) => setTyped(e.target.value)} />
      <div className="doc-filters" id="docFilters">
        <button className={kind === "all" ? "doc-fchip on" : "doc-fchip"} onClick={() => setKind("all")}>All <span className="c">{total}</span></button>
        {entries(LABEL).filter(([k]) => facets[k]).map(([k, label]) => <button key={k} className={kind === k ? "doc-fchip on" : "doc-fchip"} onClick={() => setKind(k)}>{label} <span className="c">{facets[k]}</span></button>)}
      </div>
      <div className="doc-filters" id="docGroups" role="group" aria-label="Show files">
        {entries(GROUPED).map(([k, label]) => <button key={k} className={group === k ? "doc-fchip on" : "doc-fchip"} aria-pressed={group === k} onClick={() => setGroup(k)}>{label}</button>)}
      </div>
      <div className="doc-count" id="docCount" role="status">{first ? `${docs.length}${left > 0 ? ` of ${matched}` : ""} file${matched === 1 ? "" : "s"}` : ""}</div>
      <div id="docList">{body}</div>
    </div>
  );
}
