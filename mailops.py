"""Mailbox mutations for MailMind. Trash = move to the account's Trash folder
(reversible), then drop the local row. Usage: python3 mailops.py trash <pk>
"""
import sys
import os
import imaplib

sys.path.insert(0, os.path.dirname(__file__))
import accounts
import store


def trash_message(pk):
    # read with a short-lived connection (don't hold it across the slow IMAP move)
    c = store.conn()
    row = store.get_message(c, pk)
    c.close()
    if not row:
        return False, "not found"
    acct = accounts.get(row["account"])
    dest = accounts.trash_folder(acct)
    M = accounts.connect(acct)
    try:
        M.select(f'"{row["folder"]}"', readonly=False)
        uid = str(row["uid"])
        try:
            typ, d = M.uid("MOVE", uid, f'"{dest}"')
            if typ != "OK":
                raise imaplib.IMAP4.error(str(d))
        except imaplib.IMAP4.error:  # fallback: copy + delete + expunge
            M.uid("COPY", uid, f'"{dest}"')
            M.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            M.expunge()
    finally:
        try:
            M.logout()
        except Exception:
            pass
    c = store.conn()
    store.delete_message(c, pk)
    c.commit()
    c.close()
    return True, "trashed"


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "trash":
        print(trash_message(int(sys.argv[2])))
