// The daily AI limit: an AI request you started that would go over it asks first. Requests that hit the limit while the
// question is on screen share its one answer. One the page makes on its own leaves this out, and gets the 402 as an error.
import { Button, Dialog, Modal, ModalOverlay } from "react-aria-components";
import type { OverLimit } from "../api/client";
import { createStore } from "./store";
import { usd } from "./text";

const asking = createStore<OverLimit | null>(null);
let pending: Promise<boolean> | null = null;
let settle: (yes: boolean) => void = () => {};

export function askOverLimit(answer: OverLimit): Promise<boolean> {
  if (!pending) {
    asking.set(answer);
    pending = new Promise((done) => {
      settle = (yes) => {
        asking.set(null);
        pending = null;
        done(yes);
      };
    });
  }
  return pending;
}

export function OverLimitDialog() {
  const asked = asking.useValue();
  return (
    <ModalOverlay className="do-overlay ai-limit show" isOpen={asked !== null} isDismissable onOpenChange={(open) => { if (!open) settle(false); }}>
      <Modal>
        <Dialog role="alertdialog" className="do-panel" aria-labelledby="alHd" aria-describedby="alText">
          <div className="do-hd" id="alHd">Today’s AI limit is reached</div>
          <p className="do-intro" id="alText">
            MailMind has spent {usd(asked?.spent_micros ?? 0)} on AI today, and its daily limit is {usd(asked?.limit_micros ?? 0)}. Go over the limit for this?
          </p>
          <div className="do-foot">
            <Button className="do-confirm" onPress={() => settle(true)}>Go over the limit</Button>
            <Button className="do-cancel" autoFocus onPress={() => settle(false)}>Not now</Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
