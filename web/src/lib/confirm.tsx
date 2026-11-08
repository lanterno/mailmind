// A question that wants a yes before something happens. One at a time; asking again while one is up shares its answer.
import { Button, Dialog, Modal, ModalOverlay } from "react-aria-components";
import { createStore } from "./store";

export interface Question { title: string; body: string; yes: string; no?: string }

const asking = createStore<Question | null>(null);
let pending: Promise<boolean> | null = null;
let settle: (yes: boolean) => void = () => {};

export function askConfirm(q: Question): Promise<boolean> {
  if (!pending) {
    asking.set(q);
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

export function ConfirmDialog() {
  const q = asking.useValue();
  return (
    <ModalOverlay className="do-overlay ai-limit show" isOpen={q !== null} isDismissable onOpenChange={(open) => { if (!open) settle(false); }}>
      <Modal>
        <Dialog role="alertdialog" className="do-panel" aria-labelledby="cfHd" aria-describedby="cfText">
          <div className="do-hd" id="cfHd">{q?.title}</div>
          <p className="do-intro" id="cfText">{q?.body}</p>
          <div className="do-foot">
            <Button className="btn-retry strong" onPress={() => settle(true)}>{q?.yes}</Button>
            <Button className="do-cancel" autoFocus onPress={() => settle(false)}>{q?.no ?? "Not now"}</Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
