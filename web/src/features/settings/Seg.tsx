// One choice of a few, as a row of buttons.
export function Seg<T extends string>({ value, options, label, onChange }: { value: T; options: [T, string][]; label: string; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, words]) => <button type="button" key={v} className={v === value ? "on" : ""} aria-pressed={v === value} onClick={() => onChange(v)}>{words}</button>)}
    </div>
  );
}
