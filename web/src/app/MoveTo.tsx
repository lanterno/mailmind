// m: move what is open or marked to a folder you pick, found by typing. Only the account's own folders, and of those the ones
// that take filing, as the server says of each.
import { Dialog, Modal, ModalOverlay } from "react-aria-components";
import { moveAsk } from "../features/mailbox/actions";
import { roleLabel } from "../features/mailbox/rows";
import type { Mailbox } from "../features/mailbox/useMailbox";
import { useLayer } from "../lib/backTrap";
import { Entries } from "./Palette";

export function MoveTo({ m }: { m: Mailbox }) {
  const ask = moveAsk.useValue();
  const close = () => moveAsk.set(null);
  useLayer(ask !== null, close);
  const folders = ask ? m.everyFolder.get(ask.account) ?? [] : [];
  const entries = folders
    .filter((f) => f.takes_filing && f.name !== ask?.from)
    .map((f) => ({ id: f.name, group: "", label: roleLabel(f), run: () => m.moveTo(f.name, roleLabel(f)) }));
  return (
    <ModalOverlay className="palette show" isOpen={ask !== null} isDismissable onOpenChange={(open) => { if (!open) close(); }}>
      <Modal>
        <Dialog aria-label="Move to folder" style={{ outline: "none" }}>
          <Entries entries={entries} close={close} placeholder="Move to a folder…  (↑↓ Enter)" label="Folder" listLabel="Folders" idPrefix="mv" />
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
