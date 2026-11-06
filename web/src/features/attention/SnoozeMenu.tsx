// When to bring it back: a preset, by click or by its digit, or a time of your own. One menu for the open message, a
// conversation and a marked set; whatever asks for it says what it's about and where to hang it.
import { Dialog, Popover } from "react-aria-components";
import { createStore } from "../../lib/store";
import { useDigitKeys, useFocusFirst } from "../../lib/ui/menuKeys";
import type { Snoozing, Target } from "../mailbox/actions";

const PRESETS: [string, string][] = [["later_today", "Later today"], ["tomorrow", "Tomorrow 8am"], ["weekend", "This weekend"], ["next_week", "Next week"]];

export interface SnoozeAsk { target: Target; rows: number[]; anchor: Element | null; canUnsnooze: boolean }
export const snoozeAsk = createStore<SnoozeAsk | null>(null);

// A verb the window is too narrow to show opens under ⋯.
export function visibleAnchor(el: Element | null): Element | null {
  return el?.getClientRects().length ? el : document.querySelector("#reader .rd-more") ?? el;
}

function Choices({ ask, pick, unsnooze }: { ask: SnoozeAsk; pick: (when: Snoozing) => void; unsnooze: () => void }) {
  const inside = useFocusFirst<HTMLDivElement>();
  useDigitKeys(PRESETS.length, (i) => pick({ preset: PRESETS[i]![0] }));
  return (
    <div ref={inside} className="rac-col">
      {PRESETS.map(([key, label], i) => <button key={key} onClick={() => pick({ preset: key })}><span className="snz-n">{i + 1}</span> {label}</button>)}
      <label className="snz-custom">Custom… <input type="datetime-local" id="snzWhen" onChange={(e) => { if (e.target.value) pick({ until: e.target.value }); }} /></label>
      {ask.canUnsnooze && <button onClick={unsnooze}>Unsnooze</button>}
    </div>
  );
}

export function SnoozeMenu({ onSnooze, onUnsnooze }: { onSnooze: (t: Target, when: Snoozing, rows: number[]) => void; onUnsnooze: () => void }) {
  const ask = snoozeAsk.useValue();
  const close = () => snoozeAsk.set(null);
  if (!ask) return null;
  const pick = (when: Snoozing) => {
    close();
    onSnooze(ask.target, when, ask.rows);
  };
  return (
    <Popover className="snooze-menu rac" isOpen triggerRef={{ current: visibleAnchor(ask.anchor) ?? document.body }} placement="bottom start" offset={4} onOpenChange={(open) => { if (!open) close(); }}>
      <Dialog aria-label="Snooze until">
        <Choices ask={ask} pick={pick} unsnooze={() => { close(); onUnsnooze(); }} />
      </Dialog>
    </Popover>
  );
}
