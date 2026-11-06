// The tray the top bar's ↑ opens: changes waiting for a server, and what the outbox holds, each with its way out.
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, get, post } from "../../api/client";
import type { GetAnswers } from "../../api/types.gen";
import { toast } from "../../lib/toast";
import { fmtWhen } from "./model";

type Sent = GetAnswers["/api/outbox"]["items"][number];
type Held = GetAnswers["/api/pending"]["items"][number];

const OPS: Record<string, string> = { trash: "Move to Trash", archive: "Archive", junk: "Mark as junk", move: "Move" };
const FLAGS: Record<string, [string, string]> = { seen: ["Mark unread", "Mark read"], flagged: ["Remove star", "Star"], answered: ["Mark unanswered", "Mark answered"] };
const WHY: Record<string, string> = { refused: "the server refused it", no_folder: "no folder to move it to", "account removed": "its account is gone" };

function whatItDoes(p: Held): string {
  if (p.op === "flag") {
    const [k, on] = Object.entries(p.flags ?? {})[0] ?? [];
    return (k && FLAGS[k]?.[on ? 1 : 0]) || "Change";
  }
  return p.op === "move" && p.dest ? `Move to ${p.dest}` : OPS[p.op] ?? "Change";
}

const useTray = () => {
  const outbox = useQuery({ queryKey: ["outbox"], queryFn: () => get("/api/outbox?limit=50") });
  const pending = useQuery({ queryKey: ["pending"], queryFn: () => get("/api/pending") });
  return { items: outbox.data?.items ?? [], held: pending.data?.items ?? [], waiting: pending.data?.waiting ?? 0, stuck: pending.data?.stuck ?? 0 };
};

function HeldRow({ p, readOnly, act }: { p: Held; readOnly: boolean; act: (what: "retry" | "discard", id: number) => void }) {
  const stuck = p.state === "error";
  const why = stuck ? WHY[p.error ?? ""] ?? "couldn’t reach the server" : readOnly ? "waits for a full run" : p.attempts ? `tried ${p.attempts}×, trying again` : "once the server is back";
  return (
    <div className="ob-row">
      <span className={`ob-st ${stuck ? "error" : p.state === "running" ? "sending" : "queued"}`}>{stuck ? "stuck" : p.state === "running" ? "trying" : "waiting"}</span>
      <span className="ob-sub">{whatItDoes(p)}: {p.subject || "(no subject)"}<br /><span className="ob-to ob-why">{why} · {p.account}</span></span>
      {stuck && <button className="ob-retry" onClick={() => act("retry", p.id)}>Retry</button>}
      <button className="ob-retry" onClick={() => act("discard", p.id)}>Discard</button>
    </div>
  );
}

function SentRow({ o, call }: { o: Sent; call: (path: string, id: number, ok: (ok: boolean) => string, fail: string) => void }) {
  const to = (o.to ?? []).join(", ") + (o.bcc_count ? ` +${o.bcc_count} bcc` : "");
  const label = o.scheduled ? "scheduled" : o.unsure ? "unsure" : o.state;
  return (
    <div className="ob-row">
      <span className={`ob-st ${o.scheduled ? "scheduled" : o.state}`}>{label}</span>
      <span className="ob-sub">{o.subject}<br /><span className={o.error ? "ob-to ob-why" : "ob-to"}>to {to}{o.scheduled ? ` · ${fmtWhen(o.next_attempt_at)}` : ""}{o.error ? ` · ${o.error}` : ""}</span></span>
      {o.scheduled && (
        <>
          <button className="ob-retry" onClick={() => call("/api/outbox/sendnow", o.id, () => "Sending now…", "Couldn’t send now")}>Send now</button>
          <button className="ob-retry" onClick={() => call("/api/outbox/cancel", o.id, (ok) => (ok ? "Scheduled send cancelled" : "Too late to cancel"), "Cancel failed")}>Cancel</button>
        </>
      )}
      {o.state === "error" && <button className="ob-retry" onClick={() => call("/api/outbox/retry", o.id, () => "Retrying…", "Retry failed")}>{o.unsure ? "Send again" : "Retry"}</button>}
    </div>
  );
}

export function OutboxButton({ readOnly }: { readOnly: boolean }) {
  const qc = useQueryClient();
  const { items, held, waiting, stuck } = useTray();
  const [open, setOpen] = useState(false);
  const again = () => {
    void qc.invalidateQueries({ queryKey: ["outbox"] });
    void qc.invalidateQueries({ queryKey: ["pending"] });
  };
  useEffect(() => {
    if (!open) return;
    again();
    const onDown = (e: MouseEvent) => {
      if (!(e.target as Element).closest?.("#outboxFly,#outboxBtn")) setOpen(false);
    };
    document.addEventListener("click", onDown);
    return () => document.removeEventListener("click", onDown);
  }, [open]);
  const active = items.filter((o) => o.state === "queued" || o.state === "sending").length + waiting;
  const failed = items.filter((o) => o.state === "error").length + stuck;
  const act = async (what: "retry" | "discard", id: number) => {
    try {
      const r = await post(`/api/pending/${what}`, { id });
      toast(!r.ok ? "It has already settled" : what === "retry" ? "Trying again…" : "Discarded: it stays as it was");
    } catch (e) {
      toast(e instanceof ApiError && e.status === 409 ? "It’s being tried now. Try again in a moment" : "It has already settled");
    }
    again();
    void qc.invalidateQueries({ queryKey: ["messages"] });
    void qc.invalidateQueries({ queryKey: ["folders"] });
  };
  const call = async (path: string, id: number, ok: (ok: boolean) => string, fail: string) => {
    try {
      const r = await post(path as "/api/outbox/cancel", { id });
      toast(ok(Boolean(r.ok)));
    } catch {
      toast(fail);
    }
    setTimeout(again, 300);
  };
  if (!active && !failed && !open) return null;
  return (
    <>
      <button className={failed ? "tbtn icon has-fail" : "tbtn icon"} id="outboxBtn" title="Outbox and changes waiting" aria-label="Outbox and changes waiting" aria-expanded={open} onClick={() => setOpen(!open)}>
        ↑<span id="outboxN" className="obn">{active + failed}</span>
      </button>
      {open && (
        <div className="outbox-fly" id="outboxFly">
          {held.length > 0 && <h4>Changes waiting</h4>}
          {held.map((p) => <HeldRow key={p.id} p={p} readOnly={readOnly} act={(w, i) => void act(w, i)} />)}
          {(items.length > 0 || !held.length) && <h4>Outbox</h4>}
          {!items.length && !held.length && <div className="ob-row"><span className="ob-sub">Nothing queued.</span></div>}
          {items.map((o) => <SentRow key={o.id} o={o} call={(p, i, k, f) => void call(p, i, k, f)} />)}
        </div>
      )}
    </>
  );
}
