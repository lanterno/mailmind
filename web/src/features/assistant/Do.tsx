// The preview a bulk action must pass. It says what would move and how many, and a destructive one takes a second press.
import { useQueryClient } from "@tanstack/react-query";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { go } from "../../app/route";
import { clearCommandFromBox, clearSearchBox } from "../../app/TopBar";
import { Icon } from "../../lib/ui/Icon";
import { confirmPlan, doing, runDo, stopDoing, VERB_LABEL, type Doing } from "./ask";
import { useLayer } from "../../lib/backTrap";

const search = (account: string) => (q: string) => go({ source: { kind: "search", q, account }, opened: null });
const dismiss = () => {
  stopDoing();
  clearCommandFromBox();
};

function Body({ at, account }: { at: Doing; account: string }) {
  const qc = useQueryClient();
  const hd = <div className="do-hd"><Icon name="wind" /> Bulk action</div>;
  if (at.state === "loading") return <div className="do-load" role="status"><span className="spin">↻</span> Planning “{at.cmd}”…</div>;
  if (at.state === "error") {
    return (
      <>
        {hd}<div className="do-err">Couldn’t plan that: {at.error}</div>
        <div className="do-foot">
          <button className="btn-retry strong" onClick={() => void runDo(at.cmd, search(account))}>Retry</button>
          <button className="do-cancel" onClick={dismiss}>Close</button>
        </div>
      </>
    );
  }
  if (at.state === "intro") {
    return (
      <>
        {hd}
        <div className="do-intro">
          Type a command in plain English after the <b>!</b> and press Enter — you’ll see exactly what it affects <b>before anything happens</b>.
          <div className="do-egs"><span>archive newsletters older than a week</span><span>mark everything from LinkedIn as read</span><span>snooze all receipts until next week</span></div>
        </div>
        <div className="do-foot"><button className="do-cancel" onClick={dismiss}>Close</button></div>
      </>
    );
  }
  if (at.state === "nofilter") {
    return (
      <>
        {hd}
        <div className="do-intro">I couldn’t turn that into a <b>safe</b> bulk action. Try naming a scope <i>and</i> a verb — e.g. “archive newsletters older than a week”. Drop the <b>!</b> to search instead.</div>
        <div className="do-foot"><button className="do-cancel" onClick={dismiss}>Close</button></div>
      </>
    );
  }
  const d = at.plan, verb = d.verb, label = VERB_LABEL[verb];
  const danger = verb === "trash" || verb === "junk";
  const count = d.count ?? 0, acting = d.capped ? d.cap : count, sample = d.sample ?? [], more = count - sample.length;
  const press = () => {
    if (danger && !at.armed) doing.set({ ...at, armed: true });
    else void confirmPlan(qc, d, clearSearchBox);
  };
  return (
    <div className={danger ? "do-plan do-plan-danger" : "do-plan"}>
      <div className="do-hd"><Icon name="wind" /> {label} <b>{count}</b> message{count === 1 ? "" : "s"}?</div>
      <div className="do-scope">matching <code>{d.filter}</code>{verb === "snooze" && <> · until <b>{d.snooze_preset.replace(/_/g, " ")}</b></>}</div>
      <div className="do-srcs">
        {sample.map((s, i) => <div className="do-src" key={i}><span className="do-src-from">{s.from}</span><span className="do-src-subj">{s.subject}</span><span className="do-src-when">{s.when}</span></div>)}
        {more > 0 && <div className="do-more">+ {more} more</div>}
      </div>
      {d.capped && <div className="do-cap"><Icon name="alert" /> Acts on the most recent <b>{d.cap}</b> of {count} — run “!{at.cmd}” again to continue.</div>}
      {danger && <div className="do-danger-note">{verb === "trash" ? "Moves to Trash" : "Marks as junk"} — reversible with <b>Undo</b> (z).</div>}
      {(verb === "read" || verb === "unread" || verb === "star") && <div className="do-note">Not tracked by Undo — reverse it with the opposite command.</div>}
      <div className="do-foot">
        <button className={`btn-retry strong${danger ? " danger" : ""}${at.armed ? " armed" : ""}`} onClick={press}>{at.armed ? "Click again to confirm" : `${label} ${acting}`}</button>
        <button className="do-cancel" onClick={dismiss}>Cancel</button>
        <button className="do-assearch" onClick={() => { stopDoing(); search(account)(d.filter || ""); }}>Show as search</button>
      </div>
    </div>
  );
}

export function DoPanel({ account }: { account: string }) {
  const at = doing.useValue();
  useLayer(at !== null, dismiss);
  return (
    <ModalOverlay className="do-overlay show" isOpen={at !== null} isDismissable onOpenChange={(open) => { if (!open) dismiss(); }}>
      <Modal className="modal-through">
        <Dialog aria-label="Bulk action" className="do-panel">
          <div id="doPanelBody">{at && <Body at={at} account={account} />}</div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
