// What the keys do, and the one setting WCAG 2.1.4 asks for: single keys off.
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { singleKeys } from "../../lib/prefs";
import { createStore } from "../../lib/store";
import { setTheme, theme, THEME_LABEL, type ThemeName } from "../../lib/theme";
import { Icon } from "../../lib/ui/Icon";
import { useLayer } from "../../lib/backTrap";
import type { Command } from "../../app/commands";

export const helpOpen = createStore(false);
const THEMES: ThemeName[] = ["day", "dusk", "night"];

// What the app's own list of commands says about keys, so the legend can't drift from what they do: `commands` are those
// with keys, and `go` where g then a letter goes.
export interface Legend {
  commands: Command[];
  go: { key: string; where: string }[];
}

const GLYPH: Record<string, string> = { ArrowDown: "↓", ArrowUp: "↑", Enter: "↵", Backspace: "⌫", Delete: "⌦" };
// Keys no command owns: Esc steps back from whatever is open, and the rest hold down ⌘ (the palette's, the composer's).
const NOT_COMMANDS: [string[], string][] = [[["⌘K"], "Commands"], [["Esc"], "Back or close"], [["⌘↵"], "Send, while composing"], [["⌘S"], "Save the draft, while composing"]];

function Row({ keys, children }: { keys: string[]; children: React.ReactNode }) {
  return <><span className="legkeys">{keys.map((k) => <kbd key={k}>{GLYPH[k] ?? k}</kbd>)}</span><span>{children}</span></>;
}

function Keys({ legend }: { legend: () => Legend }) {
  const singleKeysOn = singleKeys.useValue();
  const current = theme.useValue();
  const { commands, go } = legend();
  const groups = [...new Set(commands.map((c) => c.group))];
  return (
    <>
      <h3 id="legendTitle">Keyboard</h3>
      <div className="legcols">
        {groups.map((group, i) => (
          <div key={group}>
            <div className="legh">{group}</div>
            <div className="g">
              {commands.filter((c) => c.group === group).map((c) => <Row key={c.id} keys={c.keys ?? []}>{c.legend ?? c.label}</Row>)}
              {i === groups.length - 1 && (
                <>
                  <Row keys={["g"]}>Go to folder, then {go.map(({ key, where }) => <span key={key}><kbd>{key}</kbd> {where} </span>)}</Row>
                  {NOT_COMMANDS.map(([keys, words]) => <Row key={words} keys={keys}>{words}</Row>)}
                </>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="legh">Search operators</div>
      <div className="legsearch"><code>from:</code> <code>to:</code> <code>cc:</code> <code>subject:</code> <code>body:</code> <code>is:unread</code> <code>is:flagged</code> <code>category:finance</code> <code>is:newsletter</code> <code>is:snoozed</code> <code>bucket:receipt</code> <code>has:attachment</code> <code>in:Sent</code> <code>account:</code> <code>before:</code>/<code>after:</code> <code>"quoted phrase"</code> <code>-exclude</code></div>
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
          checked={!singleKeysOn}
          onChange={(e) => singleKeys.set(!e.target.checked)}
        /> Disable single-key shortcuts (use ⌘/Ctrl chords only)
      </label>
    </>
  );
}

export function Help({ legend, onGuide }: { legend: () => Legend; onGuide: () => void }) {
  const open = helpOpen.useValue();
  useLayer(open, () => helpOpen.set(false));
  return (
    <ModalOverlay className="legend show" isOpen={open} isDismissable onOpenChange={helpOpen.set}>
      <Modal className="modal-through">
        <Dialog aria-labelledby="legendTitle" className="box">
          <Keys legend={legend} />
          <button type="button" className="abtn" id="legGuide" onClick={() => { helpOpen.set(false); onGuide(); }}>Show me around</button>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
