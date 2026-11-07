// Today's brief: what the Inbox opens on when nothing is open. The narrative is Claude's; the cards, tallies and side
// column come from the digest, so they stand with AI off.
import { useEffect } from "react";
import { go, type DigestList, type ListSource } from "../../app/route";
import { minute } from "../../lib/clock";
import { askConfirm } from "../../lib/confirm";
import { avatarHue, initialsOf, shortAddress } from "../../lib/text";
import { Icon } from "../../lib/ui/Icon";
import { Scene } from "../../lib/ui/Scene";
import { answer, peekCards, useDraftChip } from "../compose";
import { filing, bulk } from "../mailbox";
import { snoozeAsk } from "../attention";
import type { Mailbox } from "../mailbox";
import { LIMIT, useBrief, useSide } from "./data";
import { BRIEF_CAP, commitments, eventWhen, fromDigest, greeting, nameIn, needsYou, safeToFile, topicItems, waitAge, type DigestItem } from "./model";
import { panel } from "./Panels";

const narrow = () => matchMedia("(max-width:820px)").matches;
export const AI_PRIVACY = "With AI on, the text of a message goes to Anthropic only when it is summarized, briefed or drafted; nothing else leaves this machine.";

// Where a list from the digest goes back to.
let cameFrom: ListSource | null = null;
export const backFromDigest = (accounts: string[]): ListSource | null =>
  cameFrom ?? (accounts[0] ? { kind: "folder", account: accounts[0], folder: "INBOX" } : null);

export function openDigestList(from: ListSource | null, list: DigestList, items: DigestItem[]) {
  cameFrom = from;
  go({ source: { kind: "digest", ...list }, opened: !narrow() && items[0] ? { kind: "message", pk: items[0].pk } : null });
}

function AiOff() {
  return (
    <div className="bf-aioff">
      <p><b>AI is off.</b> Summaries, this brief and reply drafts come from Claude, and MailMind has no Anthropic API key. Your mail works the same without it.</p>
      <p className="m">To turn it on, put an <code>anthropic_api_key</code> in <code>mailmind.config.json</code>, or set <code>ANTHROPIC_API_KEY</code>, then restart MailMind.</p>
      <p className="m pv"><Icon name="lock" /><span>{AI_PRIVACY}</span></p>
    </div>
  );
}

function NeedCard({ it, m, multi }: { it: DigestItem; m: Mailbox; multi: boolean }) {
  const chip = useDraftChip(it.pk);
  const subject = it.subject || "(no subject)";
  const action = String(it.action ?? "").trim();
  return (
    <article className="bf-need" data-pk={it.pk}>
      <span className="av" style={{ "--h": avatarHue(it.from_addr || it.from_name) } as React.CSSProperties} aria-hidden="true">{initialsOf(it.from_name, it.from_addr)}</span>
      <div style={{ minWidth: 0 }}>
        <div className="bf-n1">
          <b>{it.from_name || it.from_addr || "—"}</b>
          {multi && <span className="acctchip">{it.alias || shortAddress(it.account)}</span>}
          <time>{it.when}</time>
        </div>
        <button className="bf-subj" onClick={() => m.openAny(it.pk)}>{subject}</button>
        {it.summary && <p className="bf-gist">{it.summary}</p>}
        {action && <span className="rchip need">{action}</span>}
        <footer className="bf-acts">
          {chip === "ready"
            ? <button className="bf-act ai" title="Opens the pre-written reply in the composer; nothing sends until you do" onClick={() => void answer(it.pk)}><Icon name="wind" />Use draft</button>
            : <button className="bf-act" onClick={() => void answer(it.pk)}><Icon name="reply" />Reply</button>}
          <button className="bf-act quiet" onClick={() => m.openAny(it.pk)}><Icon name="eye" />Read</button>
          <span className="sp" />
          <button className="bf-ic" title="Snooze" aria-label={`Snooze “${subject}”`} onClick={(e) => snoozeAsk.set({ target: { pk: it.pk }, rows: [it.pk], anchor: e.currentTarget, canUnsnooze: false })}><Icon name="clock" /></button>
          <button className="bf-ic" title="Archive" aria-label={`Archive “${subject}”`} onClick={() => m.file({ pk: it.pk }, "archive", "card")}><Icon name="archive" /></button>
          <button className="bf-ic del" title="Delete" aria-label={`Delete “${subject}”`} onClick={() => m.file({ pk: it.pk }, "trash", "card")}><Icon name="trash" /></button>
        </footer>
      </div>
    </article>
  );
}

function Side({ m, multi }: { m: Mailbox; multi: boolean }) {
  const { data } = useSide();
  const now = new Date(minute.useValue());
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
              <div className="bs-ev" key={`${e.pk}|${e.start}|${i}`}>
                <time dateTime={e.start}>{big}<small>{small}</small></time>
                <div style={{ minWidth: 0 }}>
                  <button className="bs-t" onClick={() => m.openAny(e.pk)}>{e.summary || "(no title)"}</button>
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
              <button className="bs-wt" key={w.pk} onClick={() => m.openAny(w.pk)}>
                <span className="av" style={{ "--h": avatarHue(w.to) } as React.CSSProperties} aria-hidden="true">{initialsOf(who, w.to)}</span>
                <span style={{ minWidth: 0 }}><b>{who}</b><small>{w.subject || "(no subject)"} · {waitAge(w.ts ?? 0, now)}{multi ? ` · ${m.aliases[w.account] || shortAddress(w.account)}` : ""}</small></span>
              </button>
            );
          })}
          {waiting.length > 3 && <button className="bs-more" onClick={() => m.goToFolder("__waiting__")}>See all {waiting.length}</button>}
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

export function BriefPane({ m }: { m: Mailbox }) {
  const hidden = filing.useValue();
  const items = (m.digest?.items ?? []).filter((it) => !hidden.has(it.pk));
  const ai = m.digest?.ai;
  const aiOn = ai ? ai.engine !== "none" : undefined;
  const { brief, writing, error, write } = useBrief(aiOn);
  const needs = needsYou(items);
  const shown = needs.slice(0, BRIEF_CAP);
  const tidy = safeToFile(items);
  const commits = commitments(items).length;
  const multi = m.accounts.length > 1;
  const waiting = m.folders?.waiting ?? 0, later = m.folders?.replylater ?? 0;
  const topics = (brief?.groups ?? []).map((g) => ({ label: g.label, items: topicItems(g.pks, items) })).filter((g) => g.items.length);
  const peekList = shown.map((x) => x.pk).join();
  useEffect(() => {
    if (peekList) void peekCards(peekList.split(",").map(Number));
  }, [peekList]);

  const failing = ai && ai.engine !== "none" && ai.last_ok === false && ai.last_error;
  let narrative: React.ReactNode;
  if (aiOn === false && !brief?.narrative) narrative = <AiOff />;
  else if (writing) narrative = <p className="bf-wait"><span className="spin"><Icon name="wind" /></span> Reading your inbox…</p>;
  else if (brief?.narrative) narrative = <p className="ai-tldr bf-narr">{brief.narrative}</p>;
  else if (error === LIMIT) narrative = <p className="bf-wait">Today’s AI limit is reached, so the brief isn’t written yet. <button className="ai-rerun" onClick={() => write(false)}>Write it</button></p>;
  else if (error) narrative = <p className="bf-wait">The brief isn’t available right now ({failing || error}). <button className="ai-rerun" onClick={() => write(false)}>Try again</button></p>;
  else narrative = <p className="bf-wait">Nothing to brief yet.</p>;

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
        <button className="cab" data-brief="refresh" title="Write the brief again from your mail as it is now (runs the AI)" disabled={writing} onClick={() => write(true)}><span><Icon name="refresh" /></span> Refresh brief</button>
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
          {waiting > 0 && <button className="bf-tally" data-brief="__waiting__" onClick={() => m.goToFolder("__waiting__")}><b>{waiting}</b> waiting on others</button>}
          {later > 0 && <button className="bf-tally" data-brief="__replylater__" onClick={() => m.goToFolder("__replylater__")}><b>{later}</b> to reply later</button>}
          {commits > 0 && <button className="bf-tally" data-brief="commits" onClick={() => panel.set("commits")}><Icon name="todo" /><b>{commits}</b> commitment{commits > 1 ? "s" : ""}</button>}
          <button className="bf-tally" data-brief="week" onClick={() => panel.set("week")}><Icon name="cal" />Your week</button>
        </div>
      </section>
      <div className="bf-grid">
        <div style={{ minWidth: 0 }}>
          <h2 className="bf-h" id="bfNeeds">Needs you</h2>
          {needs.length > 0
            ? (
              <>
                <div className="bf-needs">{shown.map((it) => <NeedCard key={it.pk} it={it} m={m} multi={multi} />)}</div>
                {needs.length > BRIEF_CAP && <button className="bf-tally" data-brief="needs" onClick={() => openDigestList(m.source, { list: "needs" }, needs)}>Show all {needs.length}</button>}
              </>
            )
            : <p className="bf-calm">Nothing needs you right now.</p>}
          {tidy.length > 0 && (
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
  const n = needsYou((m.digest?.items ?? []).filter((it) => !hidden.has(it.pk))).length;
  const word = n ? `${n} need${n === 1 ? "s" : ""} you` : "Nothing needs you";
  return (
    <button className={m.opened ? "brief-row" : "brief-row sel"} data-brief-row onClick={m.showBrief}>
      <span className="bthumb" aria-hidden="true"><Scene p="bt" seed={4} par="xMaxYMax slice" /></span>
      <span><b>Today’s brief</b><small className={n ? "sun" : undefined}>{word}</small></span>
    </button>
  );
}
