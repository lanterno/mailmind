import { Button, Dialog, DialogTrigger, Popover } from "react-aria-components";
import { FONTS, readPrefs, setReadPref, type ReadPrefs } from "../mailbox";

type Choice<K extends keyof ReadPrefs> = [ReadPrefs[K], string, string?];

function Segment<K extends keyof ReadPrefs>({ name, label, options, faces }: { name: K; label: string; options: Choice<K>[]; faces?: boolean }) {
  const current = readPrefs.useValue()[name];
  return (
    <div className="rs-row">
      <span className="rs-lab" id={`rs-${name}`}>{label}</span>
      <div className="seg" role="group" aria-labelledby={`rs-${name}`}>
        {options.map(([value, text, spoken]) => (
          <button
            key={String(value)}
            type="button"
            className={`${current === value ? "on" : ""}${name === "size" ? ` rs-sz-${String(value)}` : ""}`}
            aria-pressed={current === value}
            aria-label={spoken}
            style={faces ? { fontFamily: FONTS[value as keyof typeof FONTS] } : undefined}
            onClick={() => setReadPref(name, value)}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

export function ReadMenu() {
  return (
    <DialogTrigger>
      <Button className="cab" data-readset="" aria-label="Reading settings" {...{ title: "Reading settings — column width, text and mail colors" }}><span className="rs-aa">Aa</span></Button>
      <Popover className="read-menu rac" placement="bottom end" offset={6}>
        <Dialog aria-labelledby="readMenuH">
          <h3 id="readMenuH">Reading settings</h3>
          <Segment name="width" label="Column width" options={[["narrow", "Narrow"], ["medium", "Medium"], ["wide", "Wide"]]} />
          <Segment name="font" label="Plain-text face" faces options={[["sans", "Sans"], ["serif", "Serif"], ["mono", "Mono"]]} />
          <Segment name="size" label="Plain-text size" options={[["s", "A", "Small"], ["m", "A", "Medium"], ["l", "A", "Large"], ["xl", "A", "Extra large"]]} />
          <Segment name="colors" label="Mail colors" options={[["auto", "Auto"], ["dark", "Dark"], ["original", "Original"]]} />
          <p className="rs-note">Face and size apply to plain-text mail; rich mail keeps the sender’s own type. Auto shows rich mail in its own colors and plain text in the app’s.</p>
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}
