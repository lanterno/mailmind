// A summary above the mail: the one ingest already wrote, shown at once with no AI call, or the deeper one you ask for.
import { ApiError, post } from "../../api/client";
import type { PostAnswers } from "../../api/types.gen";
import { createStore } from "../../lib/store";
import { askOverLimit } from "../../lib/overLimit";
import { Icon } from "../../lib/ui/Icon";
import type { Target } from "./actions";
import type { Row } from "./rows";

type Summary = PostAnswers["/api/ai/summarize"];
type Card = { kind: "loading" } | { kind: "unavailable" } | { kind: "error"; error: string } | { kind: "done"; d: Summary };

const cards = createStore<{ key: string; card: Card } | null>(null);
const inFlight = new Set<string>();

const keyOf = (t: Target) => JSON.stringify(t);

export async function summarize(target: Target, force = false) {
  const key = keyOf(target);
  if (inFlight.has(key)) return;   // a second i asks nothing more of Claude
  inFlight.add(key);
  const show = (card: Card) => {
    if (inFlight.has(key)) cards.set({ key, card });
  };
  show({ kind: "loading" });
  try {
    const d = await post("/api/ai/summarize", force ? { ...target, force: true } : target, { askOverLimit });
    if (d.ok) show({ kind: "done", d });
    else show((d as { retry?: boolean }).retry ? { kind: "unavailable" } : { kind: "error", error: (d as { error?: string }).error ?? "" });
  } catch (e) {
    show({ kind: "error", error: e instanceof ApiError ? e.message : String(e) });
  } finally {
    inFlight.delete(key);
  }
}

export function AiCard({ target, row }: { target: Target | null; row: Row | undefined }) {
  const state = cards.useValue();
  const shown = target && state?.key === keyOf(target) ? state.card : null;
  const rerun = (force = true) => {
    if (target) void summarize(target, force);
  };
  const heading = <span className="ai-mark"><Icon name="wind" />Summary</span>;
  if (!shown) {
    const sum = (row?.summary ?? "").trim(), action = (row?.action ?? "").trim();
    if (!sum && !action) return null;
    return (
      <div className="ai-card stored" id="aiCard">
        <div className="ai-hd">{heading}</div>
        {sum && <div className="ai-tldr">{sum}</div>}
        {action && <div className="ai-action">→ {action}</div>}
        <div className="ai-foot">Saved summary · <button className="ai-rerun" onClick={() => rerun(false)}><Icon name="wind" /> Full summary (i)</button></div>
      </div>
    );
  }
  if (shown.kind === "loading") {
    return (
      <div className="ai-card" id="aiCard">
        <div className="ai-hd">{heading}</div>
        <div className="rd-skel"><div className="skb" /><div className="skb" /><div className="skb short" /></div>
        <div className="rd-skelhint"><span className="spin"><Icon name="wind" /></span> Reading the thread…</div>
      </div>
    );
  }
  if (shown.kind === "unavailable") return <div className="ai-card" id="aiCard"><div className="ai-err">AI summary is unavailable right now (Claude not reachable). <button className="ai-rerun" onClick={() => rerun(false)}>Try again</button></div></div>;
  if (shown.kind === "error") return <div className="ai-card" id="aiCard"><div className="ai-err">Couldn’t summarize{shown.error ? ` — ${shown.error}` : ""}. <button className="ai-rerun" onClick={() => rerun(false)}>Retry</button></div></div>;
  const { d } = shown;
  return (
    <div className="ai-card" id="aiCard">
      <div className="ai-hd">
        {heading}
        {d.cached && <> <span className="ai-cached">cached</span></>}
        {d.stale && <> <span className="ai-stale">thread grew — <button className="ai-rerun" onClick={() => rerun()}>re-run</button></span></>}
      </div>
      <div className="ai-tldr">{d.tldr}</div>
      {d.points?.length > 0 && <ul className="ai-points">{d.points.map((p, i) => <li key={i}>{p}</li>)}</ul>}
      {d.action && <div className="ai-action">→ {d.action}</div>}
      <div className="ai-foot">{d.message_count || 1} msg · {d.model} <button className="ai-rerun" onClick={() => rerun()}>↻ re-run</button></div>
    </div>
  );
}
