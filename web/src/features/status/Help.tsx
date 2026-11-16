// What the keys do, and the one setting WCAG 2.1.4 asks for: single keys off.
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { aiOffered, aiPrivacy, offLine, PROVIDER_LABEL } from "../../lib/ai";
import type { AiStatus } from "../../lib/api/types";
import { prefs, singleKeys } from "../../lib/prefs";
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

// How MailMind uses AI, and what leaves this Mac for it: the provider chosen, or nothing. Turned off, only that nothing is sent.
function AiNote({ ai }: { ai: AiStatus | null | undefined }) {
  const baseUrl = prefs.useValue().ai_base_url;
  const sandbox = "Email bodies render inside a locked sandbox that can't run code or phone home.";
  if (!ai) return null;
  if (!aiOffered(ai)) {
    return (
      <>
        <div className="legh"><Icon name="lock" /> Your mail stays here</div>
        <div className="legnote">AI is turned off in Settings, so MailMind sends nothing to any AI service, and your mailbox stays in a database on this machine. {sandbox}</div>
      </>
    );
  }
  return (
    <>
      <div className="legh"><Icon name="wind" /> How MailMind uses AI</div>
      <div className="legnote">
        Summaries, importance scores, the daily brief, drafts and Ask are written by <b>{PROVIDER_LABEL[ai.provider]}</b>, chosen in Settings under AI
        {ai.off ? `; for now ${offLine(ai)}, and nothing is sent` : ""}. {aiPrivacy(ai.provider, baseUrl)} Your mailbox itself stays in a
        database on this machine. {sandbox}
      </div>
    </>
  );
}

function Keys({ legend, ai }: { legend: () => Legend; ai: AiStatus | null | undefined }) {
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
      <AiNote ai={ai} />
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

export function Help({ legend, ai, onGuide }: { legend: () => Legend; ai: AiStatus | null | undefined; onGuide: () => void }) {
  const open = helpOpen.useValue();
  useLayer(open, () => helpOpen.set(false));
  return (
    <ModalOverlay className="legend show" isOpen={open} isDismissable onOpenChange={helpOpen.set}>
      <Modal className="modal-through">
        <Dialog aria-labelledby="legendTitle" className="box">
          <Keys legend={legend} ai={ai} />
          <button type="button" className="abtn" id="legGuide" onClick={() => { helpOpen.set(false); onGuide(); }}>Show me around</button>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
