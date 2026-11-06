// What the keys do, and the one setting WCAG 2.1.4 asks for: single keys off.
import { useState } from "react";
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { createStore, store, stored } from "../../lib/store";
import { setTheme, theme, THEME_LABEL, type ThemeName } from "../../lib/theme";
import { Icon } from "../../lib/ui/Icon";
import { useLayer } from "../../lib/backTrap";

export const helpOpen = createStore(false);
const THEMES: ThemeName[] = ["day", "dusk", "night"];

function Keys() {
  const [noKeys, setNoKeys] = useState(() => stored("mm_nokeys") === "1");
  const current = theme.useValue();
  return (
    <>
      <h3 id="legendTitle">Keyboard</h3>
      <div className="legcols">
        <div>
          <div className="legh">Mail</div>
          <div className="g">
            <kbd>j</kbd><span>/ <kbd>k</kbd> move (<kbd>k</kbd> at the top: today’s brief) · <kbd>↵</kbd> focus reading</span>
            <kbd>[</kbd><span>folders: icons ⇄ names · drag the list edge to resize</span>
            <kbd>n</kbd><span>/ <kbd>p</kbd> next / prev unread</span>
            <kbd>r</kbd><span>reply · <kbd>a</kbd> all · <kbd>f</kbd> forward · <kbd>c</kbd> compose</span>
            <kbd>e</kbd><span>archive · <kbd>d</kbd>/<kbd>⌫</kbd>/<kbd>#</kbd> delete · <kbd>b</kbd> junk</span>
            <kbd>s</kbd><span>star · <kbd>u</kbd> unread · <kbd>x</kbd> select</span>
            <kbd>i</kbd><span>AI summary · <kbd>h</kbd> snooze (then <kbd>1</kbd>–<kbd>4</kbd>) · <kbd>w</kbd> mute thread</span>
            <kbd>g</kbd><span>go to folder → <kbd>i</kbd>nbox <kbd>s</kbd>ent <kbd>a</kbd>rchive <kbd>t</kbd>rash <kbd>k</kbd> junk <kbd>d</kbd>rafts <kbd>z</kbd> snoozed <kbd>w</kbd> waiting</span>
          </div>
        </div>
        <div>
          <div className="legh">Anywhere</div>
          <div className="g">
            <kbd>/</kbd><span>search · <kbd>?</kbd>question asks your inbox · <kbd>!</kbd>command plans a bulk action · <kbd>⌘K</kbd> commands</span>
            <kbd>z</kbd><span>undo last action (archive · delete · junk · move · snooze · mute)</span>
            <kbd>v</kbd><span>save the current search as a pinned View</span>
            <kbd>,</kbd><span>settings · <kbd>?</kbd> help · <kbd>Esc</kbd> back/close</span>
            <kbd>⌘</kbd><span>+<kbd>↵</kbd> send · +<kbd>S</kbd> draft (composing)</span>
          </div>
        </div>
      </div>
      <div className="legh">Search operators</div>
      <div className="legsearch"><code>from:</code> <code>to:</code> <code>cc:</code> <code>subject:</code> <code>body:</code> <code>is:unread</code> <code>is:flagged</code> <code>category:finance</code> <code>is:newsletter</code> <code>is:snoozed</code> <code>bucket:receipt</code> <code>has:attachment</code> <code>in:Sent</code> <code>account:</code> <code>before:</code>/<code>after:</code> <code>"quoted phrase"</code></div>
      <div className="legh"><Icon name="wind" /> How MailMind uses AI</div>
      <div className="legnote">Summaries, importance scores, the daily briefing, drafts, and ask-your-inbox are written by <b>Claude</b>, through the Claude API, when an API key is set; without one, AI is off and nothing is sent. Only the text of the mail being summarized, drafted or asked about is sent to Anthropic; your mailbox itself stays in a database on this machine. Email bodies render inside a locked sandbox that can't run code or phone home.</div>
      <div className="legtoggle legtheme" role="group" aria-label="Theme">Theme
        {THEMES.map((t) => <button type="button" key={t} aria-pressed={t === current} onClick={() => setTheme(t)}>{THEME_LABEL[t]}</button>)}
        <span>also the sun button, or ⌘K → “Theme”</span>
      </div>
      <label className="legtoggle">
        <input
          type="checkbox"
          id="legNoKeys"
          checked={noKeys}
          onChange={(e) => {
            setNoKeys(e.target.checked);
            store("mm_nokeys", e.target.checked ? "1" : "0");
          }}
        /> Disable single-key shortcuts (use ⌘/Ctrl chords only)
      </label>
    </>
  );
}

export function Help() {
  const open = helpOpen.useValue();
  useLayer(open, () => helpOpen.set(false));
  return (
    <ModalOverlay className="legend show" isOpen={open} isDismissable onOpenChange={helpOpen.set}>
      <Modal className="modal-through">
        <Dialog aria-labelledby="legendTitle" className="box"><Keys /></Dialog>
      </Modal>
    </ModalOverlay>
  );
}
