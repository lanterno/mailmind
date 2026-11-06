// The first-contact gate: senders this inbox hasn't decided on. A sender tray, not a message list.
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../../api/client";
import { avatarHue, shortAddress } from "../../lib/text";
import { toast } from "../../lib/toast";
import { Icon } from "../../lib/ui/Icon";
import { readAgain } from "../mailbox/actions";

export const useScreener = (account: string | undefined) =>
  useQuery({ queryKey: ["screener", account], queryFn: () => get(`/api/screener?account=${encodeURIComponent(account!)}`), enabled: Boolean(account) });

export function ScreenerPane({ account }: { account: string | undefined }) {
  const qc = useQueryClient();
  const { data } = useScreener(account);
  const [decided, setDecided] = useState<ReadonlySet<string>>(new Set());
  const rows = (data?.items ?? []).filter((s) => !decided.has(s.from_addr));
  const decide = async (addr: string, verdict: "keep" | "block") => {
    setDecided(new Set([...decided, addr]));
    try {
      const d = await post("/api/rules", { token: addr, verdict });
      if (!d.ok) throw new Error();
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
      {rows.map((s) => (
        <div className="scr-row" key={s.from_addr} data-scr={s.from_addr}>
          <span className="mono-dot" style={{ color: `hsl(${avatarHue(s.from_addr)} var(--mono-s) var(--mono-l))` }}>{(s.from_name || s.from_addr || "?")[0]!.toUpperCase()}</span>
          <div className="scr-who"><div className="scr-nm">{s.from_name}</div><div className="scr-ad">{s.from_addr} · {s.count} msg · {s.when}</div></div>
          <div className="scr-acts">
            <button className="scr-allow" onClick={() => void decide(s.from_addr, "keep")}><Icon name="check" /> Allow</button>
            <button className="scr-block" onClick={() => void decide(s.from_addr, "block")}><Icon name="ban" /> Screen out</button>
          </div>
        </div>
      ))}
    </div>
  );
}
