// Today's brief: what the Inbox opens on when nothing is open. The narrative is the AI's; the cards, tallies and side
// column come from the digest, so they stand with AI off.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { go, type DigestList, type ListSource } from "../../app/route";
import { aiOffered, aiSends, offLine, sendsOf } from "../../lib/ai";
import { get, post } from "../../lib/api/client";
import type { AiStatus } from "../../lib/api/types";
import { EMPTY } from "../../lib/emptyWords";
import { minute } from "../../lib/clock";
import { prefs } from "../../lib/prefs";
import { readOnlyOr } from "../../lib/readOnly";
import { copyFromId, copyId, copyOf } from "../../lib/copy";
import { askConfirm } from "../../lib/confirm";
import { store, stored } from "../../lib/store";
import { avatarHue, initialsOf, shortAddress } from "../../lib/text";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { Scene } from "../../lib/ui/Scene";
import { SetUpAi } from "../../lib/ui/SetUpAi";
import { answer, myAddresses, peekCards, useDraftChip } from "../compose";
import { filing, bulk, fromDigest, needsYou, safeToFile, topicItems, useFoldersAcross, whoOf, type DigestItem } from "../mailbox";
import { snoozeAsk } from "../attention";
import type { Mailbox } from "../mailbox";
import { LIMIT, useBrief, useSide } from "./data";
import { BRIEF_CAP, commitments, eventWhen, greeting, nameIn, needsNote, READING, summarizing, waitAge } from "./model";
import { panel } from "./Panels";
import { useTasks } from "./tasks";

const narrow = () => matchMedia("(max-width:820px)").matches;

// Where a list from the digest goes back to.
let cameFrom: ListSource | null = null;
export const backFromDigest = (accounts: string[]): ListSource | null =>
  cameFrom ?? (accounts[0] ? { kind: "folder", account: accounts[0], folder: "INBOX" } : null);

export function openDigestList(from: ListSource | null, list: DigestList, items: DigestItem[]) {
  cameFrom = from;
  go({ source: { kind: "digest", ...list }, opened: !narrow() && items[0] ? { kind: "message", ...copyOf(items[0]) } : null });
}

// AI off for want of a key, a model or a URL: why, for the provider chosen, and where to set it up. Turned off, it isn't shown.
function AiOff({ ai }: { ai: AiStatus }) {
  const p = prefs.useValue();
  return (
    <div className="bf-aioff">
      <p><b>{offLine(ai)}.</b> Summaries, this brief and reply drafts need it. Your mail works the same without it.</p>
      <p className="m"><SetUpAi /></p>
      <p className="m pv"><Icon name="lock" /><span>{aiSends(sendsOf(ai, p))}</span></p>
    </div>
  );
}

function NeedCard({ it, m, multi, ai }: { it: DigestItem; m: Mailbox; multi: boolean; ai: boolean }) {
  const written = useDraftChip(it.key);
  const chip = ai ? written : undefined;
  const subject = it.subject || "(no subject)";
  const action = String(it.action ?? "").trim();
  const who = whoOf(fromDigest(it), myAddresses(m.digest));
  return (
    <article className="bf-need" data-key={it.key} data-folder={it.folder} data-uid={it.uid}>
      <span className="av" style={{ "--h": avatarHue(who.avatarAddr || who.avatarName) } as React.CSSProperties} aria-hidden="true">{initialsOf(who.avatarName, who.avatarAddr)}</span>
      <div style={{ minWidth: 0 }}>
        <div className="bf-n1">
          <b>{who.name || "—"}</b>
          {multi && <span className="acctchip">{it.alias || shortAddress(it.account)}</span>}
          <time>{it.when}</time>
        </div>
        <button className="bf-subj" onClick={() => m.openAny(it)}>{subject}</button>
        {it.summary && <p className="bf-gist">{it.summary}</p>}
        {action && <span className="rchip need">{action}</span>}
        <footer className="bf-acts">
          {chip === "ready"
            ? <button className="bf-act ai" title="Opens the pre-written reply in the composer; nothing sends until you do" onClick={() => void answer(copyOf(it), ai)}><Icon name="wind" />Use draft</button>
            : <button className="bf-act" onClick={() => void answer(copyOf(it), ai)}><Icon name="reply" />Reply</button>}
          <button className="bf-act quiet" onClick={() => m.openAny(it)}><Icon name="eye" />Read</button>
          <span className="sp" />
          <button className="bf-ic" title="Snooze" aria-label={`Snooze “${subject}”`} onClick={(e) => snoozeAsk.set({ target: copyOf(it), rows: [copyId(it)], anchor: e.currentTarget, canUnsnooze: false })}><Icon name="clock" /></button>
          <button className="bf-ic" title="Archive" aria-label={`Archive “${subject}”`} onClick={() => m.file(copyOf(it), "archive", "card")}><Icon name="archive" /></button>
          <button className="bf-ic del" title="Delete" aria-label={`Delete “${subject}”`} onClick={() => m.file(copyOf(it), "trash", "card")}><Icon name="trash" /></button>
        </footer>
      </div>
    </article>
  );
}

// Waiting and Reply later counted where the brief opens them (`goToFolder`): every account's on All inboxes, else the
// account's own. Waiting holds what others wait on you for as well as what waits on them.
function useStacks(m: Mailbox) {
  const across = useFoldersAcross(m.allInboxes).data;
  return m.allInboxes ? across : m.folders;
}

function Side({ m, multi }: { m: Mailbox; multi: boolean }) {
  const { data } = useSide();
  const now = new Date(minute.useValue());
  const held = useStacks(m)?.waiting;
  const upcoming = data?.upcoming.slice(0, 4) ?? [], waiting = data?.waiting ?? [];
  if (!upcoming.length && !waiting.length) return null;
  return (
    <aside className="bf-side" aria-label="Your day">
      {upcoming.length > 0 && (
        <section className="bs-card" aria-label="Today and next">
          <h3><Icon name="cal" />Today &amp; next</h3>
          {upcoming.map((e, i) => {
            const [big, small] = eventWhen(e, now);
            const sub = [e.location, e.organizer].filter(Boolean).join(" · ");
            return (
              <div className="bs-ev" key={`${copyId(e)}|${e.start}|${i}`}>
                <time dateTime={e.start}>{big}<small>{small}</small></time>
                <div style={{ minWidth: 0 }}>
                  <button className="bs-t" onClick={() => m.openAny(e)}>{e.summary || "(no title)"}</button>
                  {(sub || e.invite) && <small className="m">{sub}{e.invite ? `${sub ? " · " : ""}invite` : ""}</small>}
                </div>
              </div>
            );
          })}
        </section>
      )}
      {waiting.length > 0 && (
        <section className="bs-card" aria-label="Waiting on others">
          <h3><Icon name="hourglass" />Waiting on others</h3>
          {waiting.slice(0, 3).map((w) => {
            const who = nameIn(w.to) || "—";
            return (
              <button className="bs-wt" key={copyId(w)} onClick={() => m.openAny(w)}>
                <span className="av" style={{ "--h": avatarHue(w.to) } as React.CSSProperties} aria-hidden="true">{initialsOf(who, w.to)}</span>
                <span style={{ minWidth: 0 }}><b>{who}</b><small>{w.subject || "(no subject)"} · {waitAge(w.ts ?? 0, now)}{multi ? ` · ${m.aliases[w.account] || shortAddress(w.account)}` : ""}</small></span>
              </button>
            );
          })}
          {/* It opens Waiting, so it says how many Waiting holds, as the tally does, not how many wait on others. */}
          {waiting.length > 3 && <button className="bs-more" onClick={() => m.goToFolder("__waiting__")}>See all {held ?? waiting.length}</button>}
        </section>
      )}
    </aside>
  );
}

function Hero() {
  const now = new Date(minute.useValue());
  return (
    <header className="bf-hero">
      <Scene p="bz" seed={21} par="xMaxYMax slice" />
      <div className="bf-ht">
        <div className="bf-date">{now.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</div>
        <h1>{greeting(now.getHours())}.</h1>
      </div>
    </header>
  );
}

const TIDY_DISMISSED = "mm_tidy_dismissed";

// What Tidy filed by itself, for someone who turned it on: how many, which, and one click to take it all back.
function TidyRun() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["tidy"], queryFn: () => get("/api/tidy") });
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(() => stored(TIDY_DISMISSED));
  const undo = useMutation({
    mutationFn: (group: string) => post("/api/tidy/undo", { group }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["tidy"] }),
    onError: (e) => toast(readOnlyOr(e, "Couldn’t take that back.")),
  });
  if (!data?.group || data.group === dismissed) return null;
  const group = data.group, n = data.filed;
  return (
    <div className="bf-tidy" data-tidy-run>
      <span><Icon name="wind" /> We filed <b>{n}</b> low-priority message{n === 1 ? "" : "s"} for you.</span>
      <button className="bf-tally" aria-expanded={open} onClick={() => setOpen(!open)}>Review</button>
      <button className="bf-tally" disabled={undo.isPending} onClick={() => undo.mutate(group)}>Undo</button>
      <button className="bf-tally" onClick={() => { store(TIDY_DISMISSED, group); setDismissed(group); }}>Dismiss</button>
      {open && <ul className="bf-tidied">{data.items.map((it, i) => <li key={i}><b>{it.who || "Unknown"}</b> {it.subject || "(no subject)"}</li>)}</ul>}
    </div>
  );
}

export function BriefPane({ m }: { m: Mailbox }) {
  const hidden = filing.useValue();
  const items = (m.digest?.items ?? []).filter((it) => !hidden.has(copyId(it)));
  const ai = m.digest?.ai;
  const aiOn = ai ? ai.engine !== "none" : undefined;
  const offered = aiOffered(ai);     // AI turned off: the brief is what the digest says, with nothing of AI's and no word of it
  const reading = summarizing(ai);
  const { brief, writing, error, write } = useBrief(aiOn, reading);
  const needs = needsYou(items);
  const shown = needs.slice(0, BRIEF_CAP);
  const tidy = safeToFile(items);
  const { data: tasks } = useTasks();
  const commits = commitments(items).length + (tasks?.filter((t) => !t.done).length ?? 0);
  const multi = m.accounts.length > 1;
  const stacks = useStacks(m);
  const waiting = stacks?.waiting ?? 0, later = stacks?.replylater ?? 0;
  const topics = (offered ? brief?.groups ?? [] : []).map((g) => ({ label: g.label, items: topicItems(g.messages, items) })).filter((g) => g.items.length);
  const peekList = shown.map((x) => copyId(x)).join("\n");
  useEffect(() => {
    if (offered && peekList) void peekCards(peekList.split("\n").map(copyFromId));
  }, [offered, peekList]);

  const failing = ai && ai.engine !== "none" && ai.last_ok === false && ai.last_error;
  let narrative: React.ReactNode;
  if (!offered) narrative = null;
  else if (ai && aiOn === false && !brief?.narrative) narrative = <AiOff ai={ai} />;
  else if (writing) narrative = <p className="bf-wait"><span className="spin"><Icon name="wind" /></span> Reading your inbox…</p>;
  else if (brief?.narrative) narrative = <p className="ai-tldr bf-narr">{brief.narrative}</p>;
  else if (error === LIMIT) narrative = <p className="bf-wait">Today’s AI limit is reached, so the brief isn’t written yet. <button className="btn-retry" onClick={() => write(false)}>Write it</button></p>;
  else if (error) narrative = <p className="bf-wait">The brief isn’t available right now ({failing || error}). <button className="btn-retry" onClick={() => write(false)}>Try again</button></p>;
  else if (reading) narrative = <p className="bf-wait"><span className="spin"><Icon name="wind" /></span> {READING}</p>;
  else narrative = <p className="bf-wait">{EMPTY.briefNone}</p>;

  const fileTidy = async () => {
    const n = tidy.length;
    const yes = await askConfirm({
      title: `File ${n} low-priority message${n > 1 ? "s" : ""} to Archive?`,
      body: "They stay searchable and you can undo with z.",
      yes: "File to Archive",
    });
    if (yes) void bulk(m.qc, "archive", tidy.map(fromDigest), () => false, null);
  };

  return (
    <>
      <div className="rd-actions">
        <button className="cab rd-back" title="Back to list" onClick={m.close}><span><Icon name="left" /></span> List</button>
        <span className="rd-sp" aria-hidden="true" />
        {offered && <button className="cab" data-brief="refresh" title="Write the brief again from your mail as it is now (runs the AI)" disabled={writing} onClick={() => write(true)}><span><Icon name="refresh" /></span> Refresh brief</button>}
      </div>
      <Hero />
      <section className="ai-card bf-card" aria-label="Today’s brief">
        <div className="ai-hd"><span className="ai-mark"><Icon name="wind" />Your brief</span></div>
        {narrative}
        {topics.length > 0 && (
          <div className="bf-topics" aria-label="Topics in today’s mail">
            {topics.map((g) => <button className="bf-topic" key={g.label} onClick={() => openDigestList(m.source, { list: "topic", label: g.label }, g.items)}>{g.label}<b>{g.items.length}</b></button>)}
          </div>
        )}
        <div className="bf-tallies">
          {needs.length > 0 && <button className="bf-tally sun" data-brief="needs" title="List them" onClick={() => openDigestList(m.source, { list: "needs" }, needs)}><b>{needs.length}</b> need{needs.length === 1 ? "s" : ""} you</button>}
          {waiting > 0 && <button className="bf-tally" data-brief="__waiting__" onClick={() => m.goToFolder("__waiting__")}><b>{waiting}</b> waiting</button>}
          {later > 0 && <button className="bf-tally" data-brief="__replylater__" onClick={() => m.goToFolder("__replylater__")}><b>{later}</b> to reply later</button>}
          {commits > 0 && <button className="bf-tally" data-brief="commits" onClick={() => panel.set("commits")}><Icon name="todo" /><b>{commits}</b> commitment{commits > 1 ? "s" : ""}</button>}
          {offered && <button className="bf-tally" data-brief="week" onClick={() => panel.set("week")}><Icon name="cal" />Your week</button>}
        </div>
      </section>
      <div className="bf-grid">
        <div style={{ minWidth: 0 }}>
          <h2 className="bf-h" id="bfNeeds">Needs you</h2>
          {needs.length > 0
            ? (
              <>
                <div className="bf-needs">{shown.map((it) => <NeedCard key={copyId(it)} it={it} m={m} multi={multi} ai={offered} />)}</div>
                {needs.length > BRIEF_CAP && <button className="bf-tally" data-brief="needs" onClick={() => openDigestList(m.source, { list: "needs" }, needs)}>Show all {needs.length}</button>}
              </>
            )
            : <p className="bf-calm">{reading ? READING : EMPTY.briefCalm}</p>}
          <TidyRun />
          {offered && tidy.length > 0 && (
            <div className="bf-tidy">
              <span><Icon name="wind" /> <b>{tidy.length}</b> low-priority message{tidy.length > 1 ? "s" : ""} the AI rates as safe to file: no next step, nothing asked of you.</span>
              <button className="bf-tally" data-brief="tidyreview" onClick={() => openDigestList(m.source, { list: "tidy" }, tidy)}>Review</button>
              <button className="bf-tally" data-brief="tidy" onClick={() => void fileTidy()}>File to Archive</button>
            </div>
          )}
        </div>
        <Side m={m} multi={multi} />
      </div>
    </>
  );
}

// The row above the Inbox's list that comes back to the brief.
export function BriefRow({ m }: { m: Mailbox }) {
  const hidden = filing.useValue();
  const n = needsYou((m.digest?.items ?? []).filter((it) => !hidden.has(copyId(it)))).length;
  const word = needsNote(n, summarizing(m.digest?.ai));
  return (
    <button className={m.opened ? "brief-row" : "brief-row sel"} data-brief-row onClick={m.showBrief}>
      <span className="bthumb" aria-hidden="true"><Scene p="bt" seed={4} par="xMaxYMax slice" /></span>
      <span><b>Today’s brief</b><small className={n ? "sun" : undefined}>{word}</small></span>
    </button>
  );
}
