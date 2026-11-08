// The first-contact gate: senders this inbox hasn't decided on. A sender tray, not a message list. The AI may suggest a verdict,
// on a click, for the senders it is sure of. Only a click on a row decides.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, get, post } from "../../lib/api/client";
import type { Verdict } from "../../lib/api/types";
import { askOverLimit } from "../../lib/overLimit";
import { avatarHue, shortAddress } from "../../lib/text";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { readAgain } from "../mailbox";

const VERDICT_WORD: Record<Verdict, string> = { keep: "Allow", block: "Screen out" };

export const useScreener = (account: string | undefined) =>
  useQuery({ queryKey: ["screener", account], queryFn: () => get(`/api/screener?account=${encodeURIComponent(account!)}`), enabled: Boolean(account) });

// Asked for on a click and kept for the session, so coming back to the Screener spends nothing.
function useSuggestions(account: string | undefined) {
  const { data, isFetching, refetch } = useQuery({
    queryKey: ["screenerSuggestions", account],
    queryFn: () => post("/api/screener/suggest", { account: account! }, { askOverLimit }),
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
  });
  const ask = async () => {
    const { data: answer, error } = await refetch();
    if (error) toast(error instanceof ApiError && error.aiOff ? "AI is off" : error instanceof ApiError && error.overLimit ? "Today’s AI limit is reached" : "Couldn’t get suggestions");
    else if (answer && !answer.suggestions.length) toast("The AI isn’t sure of any of these");
  };
  return { asked: data !== undefined, asking: isFetching, ask, by: new Map((data?.suggestions ?? []).map((s) => [s.from_addr, s])) };
}

export function ScreenerPane({ account }: { account: string | undefined }) {
  const qc = useQueryClient();
  const { data } = useScreener(account);
  const suggested = useSuggestions(account);
  const [decided, setDecided] = useState<ReadonlySet<string>>(new Set());
  const rows = (data?.items ?? []).filter((s) => !decided.has(s.from_addr));
  const decide = async (addr: string, verdict: Verdict) => {
    setDecided(new Set([...decided, addr]));
    try {
      const d = await post("/api/rules", { token: addr, verdict });
      toast(verdict === "keep" ? `Allowed ${shortAddress(addr)}` : `Screened out ${shortAddress(addr)}${(d as { trashed?: unknown }).trashed ? " · trashed" : ""}`);
      readAgain(qc);
      void qc.invalidateQueries({ queryKey: ["screener"] });
    } catch {
      toast("Couldn’t apply");
      setDecided(new Set([...decided].filter((a) => a !== addr)));
    }
  };
  if (!data) return <div id="rows"><div className="nores" role="status">Loading…</div></div>;
  if (!rows.length) {
    return <div id="rows"><div className="nores allclear"><div className="ac-h">Screener clear</div><div className="ac-b">Every sender in this inbox has been allowed or screened out.</div></div></div>;
  }
  return (
    <div id="rows">
      <div className="scr-bar">
        <span>{suggested.asked ? `The AI suggests a verdict for ${suggested.by.size} of these. You still decide.` : "The AI can suggest a verdict for the senders it’s sure of. You still decide."}</span>
        <button className="scr-suggest" disabled={suggested.asking} onClick={() => void suggested.ask()}><Icon name="wind" /> {suggested.asking ? "Reading…" : suggested.asked ? "Suggest again" : "Suggest"}</button>
      </div>
      {rows.map((s) => {
        const hint = suggested.by.get(s.from_addr);
        return (
          <div className={hint ? `scr-row suggested ${hint.verdict}` : "scr-row"} key={s.from_addr} data-scr={s.from_addr}>
            <span className="mono-dot" style={{ color: `hsl(${avatarHue(s.from_addr)} var(--mono-s) var(--mono-l))` }}>{(s.from_name || s.from_addr || "?")[0]!.toUpperCase()}</span>
            <div className="scr-who">
              <div className="scr-nm">{s.from_name}</div>
              <div className="scr-ad">{s.from_addr} · {s.count} msg · {s.when}</div>
              {hint && <div className="scr-hint"><Icon name="wind" /> <b>{VERDICT_WORD[hint.verdict]}?</b> {hint.why}</div>}
            </div>
            <div className="scr-acts">
              <button className={hint?.verdict === "keep" ? "scr-allow suggested" : "scr-allow"} onClick={() => void decide(s.from_addr, "keep")}><Icon name="check" /> Allow</button>
              <button className={hint?.verdict === "block" ? "scr-block suggested" : "scr-block"} onClick={() => void decide(s.from_addr, "block")}><Icon name="ban" /> Screen out</button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
