"""IMAP connect layer for MailMind. The account list + credentials now live in
`mailmind.config.json` (see config.py / mailmind.config.example.json). This
module just turns a config account into a logged-in IMAP connection and keeps
the small API (accounts/get/connect/trash_folder/preload_creds) that the rest
of the app imports.
"""
import imaplib
import ssl

import config


def accounts():
    return config.accounts()


def get(email):
    for a in accounts():
        if a["email"] == email:
            return a
    raise KeyError(email)


def trash_folder(acct):
    if acct.get("trash"):
        return acct["trash"]
    return "[Gmail]/Trash" if acct.get("kind") == "gmail" else "Trash"


def preload_creds():
    return config.preload()


class LoginUnreachable(OSError):
    """A connect-time login failure (expired/revoked credentials, Bridge restart). Subclasses
    OSError so it's a member of the offline-queue's _UNREACHABLE set — a mutation during an auth
    outage is QUEUED + retried rather than silently applied locally only."""


def imap_quote(s):
    """Wrap a value as an IMAP quoted-string so embedded quotes/specials (e.g. a malformed
    sender Message-ID like <…b'AB'…>) can't break a SEARCH/command."""
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def connect(acct, timeout=60):
    if acct["starttls"]:  # e.g. Proton Bridge: plain socket + STARTTLS, self-signed localhost cert
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        M = imaplib.IMAP4(acct["host"], acct["port"], timeout=timeout)
        M.starttls(ctx)
    else:  # direct TLS (Gmail + most hosted IMAP on 993)
        M = imaplib.IMAP4_SSL(acct["host"], acct["port"],
                              ssl_context=ssl.create_default_context(), timeout=timeout)
    try:
        M.login(acct["email"], config.resolve_credential(acct))
    except imaplib.IMAP4.error as e:   # login NO/BAD = a connection-level fault, not a per-command one
        raise LoginUnreachable(str(e)) from e
    return M
