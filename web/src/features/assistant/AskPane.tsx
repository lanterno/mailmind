// The answer to a question, in the list's place, with the mail it cites. Opening a source leaves it on screen.
import { copyId, copyOf } from "../../lib/copy";
import { shortAddress } from "../../lib/text";
import { Icon } from "../../lib/ui/Icon";
import type { Mailbox } from "../mailbox";
import { ask, type Asking } from "./ask";

export function AskPane({ at, m }: { at: Asking; m: Mailbox }) {
  const head = <div className="ai-hd"><span className="ai-mark"><Icon name="wind" />Answer</span><span className="ask-q">{at.question}</span></div>;
  if (at.state === "loading") {
    return (
      <div id="rows">
        <div className="ask-wrap"><div className="ai-card">
          {head}
          <div className="rd-skel"><div className="skb" /><div className="skb" /><div className="skb" /><div className="skb short" /></div>
          <div className="rd-skelhint" role="status"><span className="spin"><Icon name="wind" /></span> Reading your inbox…</div>
        </div></div>
      </div>
    );
  }
  if (at.state === "error") {
    return (
      <div id="rows">
        <div className="ask-wrap"><div className="ai-card"><div className="ask-err">Couldn’t answer: {at.error} <button className="ai-rerun" onClick={() => void ask(at.question)}>Retry</button></div></div></div>
      </div>
    );
  }
  const d = at.answer, sources = d.sources ?? [];
  return (
    <div id="rows">
      <div className="ask-wrap">
        <div className="ai-card">
          <div className="ai-hd"><span className="ai-mark"><Icon name="wind" />Answer</span><span className="ask-q">{d.question || at.question}</span></div>
          <div className="ask-answer">{d.answer}</div>
          <div className="ai-foot">{d.model || "claude"} · grounded in your mail · <button className="ai-rerun" onClick={() => void ask(at.question)}>↻ Ask again</button></div>
        </div>
        {sources.length > 0
          ? (
            <>
              <div className="ask-srcs-hd">{sources.length} cited source{sources.length === 1 ? "" : "s"}</div>
              <div className="ask-srcs">
                {sources.map((s) => (
                  <button className={m.openId === copyId(s) ? "ask-src reading" : "ask-src"} key={copyId(s)} onClick={() => m.openAny(copyOf(s))}>
                    <div className="ask-src-from">{s.from}{s.account ? ` · ${shortAddress(s.account)}` : ""}</div>
                    <div className="ask-src-subj">{s.subject || "(no subject)"}</div>
                    <div className="ask-src-snip">{(s.snippet || "").slice(0, 140)}</div>
                  </button>
                ))}
              </div>
            </>
          )
          : <div className="ask-srcs-hd ask-nosrc">No specific message cited.</div>}
      </div>
    </div>
  );
}
