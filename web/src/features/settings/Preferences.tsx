// How MailMind looks and behaves. Each control saves on its own, to the block the server keeps beside the accounts (lib/prefs.ts).
import { Input, Label, NumberField } from "react-aria-components";
import { LIMITS } from "../../lib/api/limits.gen";
import type { Density } from "../../lib/api/types";
import { entries } from "../../lib/entries";
import { prefs, setPrefs } from "../../lib/prefs";
import { THEME_LABEL } from "../../lib/theme";
import { Seg } from "./Seg";
import { useStepBack } from "./useStepBack";

const DENSITY_LABEL: Record<Density, string> = { comfortable: "Comfortable", cozy: "Cozy", compact: "Compact" };

function Check({ checked, onChange, children }: { checked: boolean; onChange: (on: boolean) => void; children: React.ReactNode }) {
  return <label className="pref-check"><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />{children}</label>;
}

export function PreferencesForm() {
  const p = prefs.useValue();
  useStepBack();
  return (
    <div className="set-body" id="prefsBody">
      <div className="set-form">
        <div className="lbl">Appearance</div>
        <div className="pref-line">
          <span className="pref-name">Theme</span>
          <Seg label="Theme" value={p.theme} options={entries(THEME_LABEL)} onChange={(theme) => setPrefs({ theme })} />
        </div>
        <div className="pref-line">
          <span className="pref-name">Density</span>
          <Seg label="Density" value={p.density} options={entries(DENSITY_LABEL)} onChange={(density) => setPrefs({ density })} />
        </div>
        <Check checked={p.high_contrast} onChange={(high_contrast) => setPrefs({ high_contrast })}>High contrast</Check>
      </div>
      <div className="set-form">
        <div className="lbl">Sending</div>
        <NumberField
          className="pref-line"
          minValue={LIMITS.undo_send_shortest}
          maxValue={LIMITS.undo_send_longest}
          step={1}
          value={p.undo_send_seconds}
          onChange={(undo_send_seconds) => { if (Number.isFinite(undo_send_seconds)) setPrefs({ undo_send_seconds }); }}
        >
          <Label className="pref-name">Undo Send window, in seconds</Label>
          <Input className="pref-num" id="prefUndoSend" />
        </NumberField>
        <div className="set-help">How long a message waits before it goes, so you can take it back ({LIMITS.undo_send_shortest}–{LIMITS.undo_send_longest} seconds).</div>
      </div>
      <div className="set-form">
        <div className="lbl">Keyboard</div>
        <Check checked={p.single_keys} onChange={(single_keys) => setPrefs({ single_keys })}>Single-key shortcuts</Check>
      </div>
    </div>
  );
}
