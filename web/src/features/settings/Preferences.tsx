// How MailMind looks and behaves. Each control saves on its own, to the block the server keeps beside the accounts (lib/prefs.ts).
import { useQueryClient } from "@tanstack/react-query";
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

// A field the browser fills in halves, so a half-typed time is held back and stays as typed: nothing fixes it up under the fingers.
function Time({ label, value, onChange }: { label: string; value: string; onChange: (time: string) => void }) {
  return (
    <label className="pref-line">
      <span className="pref-name">{label}</span>
      <input type="time" className="pref-time" defaultValue={value} onChange={(e) => { if (e.target.value) onChange(e.target.value); }} />
    </label>
  );
}

export function PreferencesForm() {
  const p = prefs.useValue();
  const qc = useQueryClient();
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
        <div className="lbl">Notifications</div>
        <Check checked={p.notify_new_mail} onChange={(notify_new_mail) => setPrefs({ notify_new_mail })}>New mail</Check>
        <Check checked={p.notify_meetings} onChange={(notify_meetings) => setPrefs({ notify_meetings })}>A meeting about to start</Check>
        <Check checked={p.quiet_hours} onChange={(quiet_hours) => setPrefs({ quiet_hours })}>Quiet hours</Check>
        {p.quiet_hours && (
          <>
            <Time label="Quiet from" value={p.quiet_from} onChange={(quiet_from) => setPrefs({ quiet_from })} />
            <Time label="Quiet until" value={p.quiet_until} onChange={(quiet_until) => setPrefs({ quiet_until })} />
          </>
        )}
        <div className="set-help">A desktop notification goes only while this tab is in the background and your browser allows it. In quiet hours, by this computer’s clock, none goes.</div>
      </div>
      <div className="set-form">
        <div className="lbl">AI</div>
        <NumberField
          className="pref-line"
          minValue={0.01}
          maxValue={LIMITS.ai_daily_limit_usd}
          step={0.01}
          formatOptions={{ maximumFractionDigits: 2 }}
          value={p.ai_daily_limit_usd}
          onChange={(ai_daily_limit_usd) => {
            if (Number.isFinite(ai_daily_limit_usd)) setPrefs({ ai_daily_limit_usd }, () => void qc.invalidateQueries({ queryKey: ["digest"] }));
          }}
        >
          <Label className="pref-name">Daily limit, in US dollars</Label>
          <Input className="pref-num" id="prefAiLimit" />
        </NumberField>
        <div className="set-help">Once a day’s AI has cost this much, background work stops and MailMind asks before spending more.</div>
      </div>
      <div className="set-form">
        <div className="lbl">Keyboard</div>
        <Check checked={p.single_keys} onChange={(single_keys) => setPrefs({ single_keys })}>Single-key shortcuts</Check>
      </div>
    </div>
  );
}
