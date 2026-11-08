// How MailMind looks and behaves. Each control saves on its own, to the block the server keeps beside the accounts (lib/prefs.ts).
import { entries } from "../../lib/entries";
import { prefs, setPrefs } from "../../lib/prefs";
import { THEME_LABEL } from "../../lib/theme";
import { Seg } from "./Seg";
import { useStepBack } from "./useStepBack";

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
      </div>
      <div className="set-form">
        <div className="lbl">Keyboard</div>
        <Check checked={p.single_keys} onChange={(single_keys) => setPrefs({ single_keys })}>Single-key shortcuts</Check>
      </div>
    </div>
  );
}
