// One setting that is on or off, as a box ticked beside its words.
export function Check({ checked, onChange, children }: { checked: boolean; onChange: (on: boolean) => void; children: React.ReactNode }) {
  return <label className="pref-check"><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />{children}</label>;
}
