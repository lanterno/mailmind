// The first-run guide: a few coach-marks over what's worth knowing is there. Offered once, skippable at every step, and the keys
// here are only Tab, Enter or Space on its buttons and Esc to leave, so it asks nothing of the single-key shortcuts and reads the
// same with them off. It moves nothing on its own, so reduced motion has nothing to stop but the page's own fade.
import { useEffect, useState, useSyncExternalStore } from "react";
import { Button, Dialog, Modal, ModalOverlay } from "react-aria-components";
import { useLayer } from "../../lib/backTrap";
import { useFocusFirst } from "../../lib/ui/menuKeys";
import { pref, prefsRead, setPrefs, singleKeys } from "../../lib/prefs";
import { createStore } from "../../lib/store";
import { composing } from "../compose";
import { settingsOpen } from "../../lib/settingsOpen";
import type { Mailbox } from "../mailbox";
import { coachPlace, guideOffers, guideSteps, type Box, type GuideStep } from "./guideSteps";
import { helpOpen } from "./Help";

export const guideOpen = createStore(false);
const offered = pref("guide_offered");

const CARD = { width: 340, height: 210 };

const onResize = (run: () => void) => {
  addEventListener("resize", run);
  return () => removeEventListener("resize", run);
};
const windowSize = () => `${innerWidth}x${innerHeight}`;

// What a step points at, as it stands. Hidden, or scrolled off the window, is not on screen: then it just says its words.
function boxOf(selectors: string[]): Box | null {
  for (const selector of selectors) {
    const r = document.querySelector(selector)?.getBoundingClientRect();
    if (r && r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight) {
      return { top: r.top, left: r.left, width: r.width, height: r.height };
    }
  }
  return null;
}

// Mounted fresh for each step and each window size, so what it reads off the page is read again for that.
function Spot({ step, children }: { step: GuideStep; children: React.ReactNode }) {
  const inside = useFocusFirst<HTMLDivElement>(".abtn.prim");     // Next, so Enter walks through and Tab starts inside
  const target = boxOf(step.at);
  const card = { width: Math.min(CARD.width, innerWidth - 24), height: CARD.height };
  const at = coachPlace(target, { width: innerWidth, height: innerHeight }, card);
  return (
    <>
      <div
        className={target ? "guide-spot" : "guide-spot bare"}
        style={target ? { top: target.top - 6, left: target.left - 6, width: target.width + 12, height: target.height + 12 } : undefined}
        aria-hidden="true"
      />
      <div className="guide-card" ref={inside} style={{ top: at.top, left: at.left, width: card.width }}>{children}</div>
    </>
  );
}

function Steps({ steps, end }: { steps: GuideStep[]; end: () => void }) {
  const [n, setN] = useState(0);
  const size = useSyncExternalStore(onResize, windowSize);
  const step = steps[Math.min(n, steps.length - 1)]!;
  const last = n >= steps.length - 1;
  return (
    <Spot key={`${step.id}:${size}`} step={step}>
      <div aria-live="polite">
        <div className="guide-n">{n + 1} of {steps.length}</div>
        <h2 className="guide-t" id="guideTitle">{step.title}</h2>
        <p className="guide-b">{step.text}</p>
      </div>
      <div className="guide-foot">
        {n > 0 && <Button className="abtn" onPress={() => setN(n - 1)}>Back</Button>}
        <span className="sp" />
        {!last && <Button className="abtn" onPress={end}>Skip</Button>}
        <Button className="abtn prim" onPress={() => (last ? end() : setN(n + 1))}>{last ? "Done" : "Next"}</Button>
      </div>
    </Spot>
  );
}

export function Guide({ m }: { m: Mailbox }) {
  const open = guideOpen.useValue();
  const singleKeysOn = singleKeys.useValue();
  const read = prefsRead.useValue();
  const said = offered.useValue();
  const settings = settingsOpen.useValue();
  const help = helpOpen.useValue();
  const offer = guideOffers({
    prefsRead: read,
    offered: said,
    accounts: m.accounts.length,
    inboxHasMail: m.inInbox && !m.searching && !m.opened && m.all.length > 0,
    busy: open || settings || help,
  });
  useEffect(() => {
    if (!offer || composing.isOpen()) return;
    setPrefs({ guide_offered: true });      // offered, not finished: Skip or a reload halfway both count
    guideOpen.set(true);
  }, [offer]);
  const end = () => guideOpen.set(false);
  useLayer(open, end);
  if (!open) return null;
  const steps = guideSteps({ ai: m.digest?.ai?.engine !== "none", singleKeys: singleKeysOn });
  return (
    <ModalOverlay className="guide" isOpen isDismissable={false} onOpenChange={guideOpen.set}>
      <Modal>
        <Dialog aria-labelledby="guideTitle" className="guide-dlg">
          <Steps steps={steps} end={end} />
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
