"""What an account's server can do, and its quirks: the ProviderProfile.

It is resolved from the account's kind ("gmail", "proton" or "imap") and where it connects, plus,
once a connection is open, the server's CAPABILITY list. Code asks the profile instead of
checking `kind == "gmail"` itself.

Gmail files mail sent through its SMTP in Sent on its own, so an APPEND would make a second copy;
archives by dropping the \\Inbox label, the message staying in All Mail; threads by X-GM-THRID; and
names its Trash and Spam "[Gmail]/Trash" and "[Gmail]/Spam".

Proton Mail Bridge listens on this machine, behind a self-signed certificate. It allows few
connections per account and breaks on a bulk EXPUNGE, so a pooled connection is recycled after 90
seconds or 40 uses, against 600 seconds or 200 uses elsewhere; and it marks a message read when it
moves to Trash. An account of another kind served by a loopback STARTTLS server is taken for
Bridge too.
"""
from dataclasses import dataclass, replace
from urllib.parse import quote
from typing import Any

_LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


@dataclass(frozen=True)
class ProviderProfile:
    kind: str
    saves_sent: bool = False                 # SMTP-sent mail lands in Sent without an APPEND
    archive_is_label_removal: bool = False   # archive = drop \Inbox; the message stays in All Mail
    gmail_threads: bool = False              # X-GM-THRID names each message's conversation
    bridge: bool = False                     # Proton Mail Bridge, or a server like it on loopback
    bulk_expunge_unsafe: bool = False        # a bulk EXPUNGE breaks the server
    marks_trash_read: bool = False           # a move to Trash sets \Seen
    trash_fallback: str = "Trash"            # the Trash folder when the config names none
    junk_fallback: str | None = None         # the Spam folder when LIST shows none
    recycle_seconds: int = 600               # a pooled connection is replaced after this long
    recycle_uses: int = 200                  # or after this many borrows
    # from CAPABILITY; None until a connection said
    special_use: bool | None = None
    condstore: bool | None = None
    move: bool | None = None
    uidplus: bool | None = None
    idle: bool | None = None

    def with_capabilities(self, capabilities):
        """This profile with what a server's CAPABILITY list says (names, any case)."""
        caps = {str(c).upper() for c in capabilities or ()}
        return replace(self, special_use="SPECIAL-USE" in caps, condstore="CONDSTORE" in caps,
                       move="MOVE" in caps, uidplus="UIDPLUS" in caps, idle="IDLE" in caps)

    def deep_link(self, *, account, message_id, subject, webmail=None):
        """A link that opens the message in the provider's own web client, "" when there is none.
        Proton's can only search by subject; another server's opens its webmail, when the config
        names one."""
        mid = (message_id or "").strip().strip("<>")
        if self.kind == "gmail" and mid:
            return f"https://mail.google.com/mail/u/?authuser={quote(account)}#search/rfc822msgid:{quote(mid)}"
        if self.kind == "proton":
            return f"https://mail.proton.me/u/0/almost-all-mail#keyword={quote((subject or '')[:64])}"
        return webmail or ""


_GMAIL = ProviderProfile("gmail", saves_sent=True, archive_is_label_removal=True, gmail_threads=True,
                         trash_fallback="[Gmail]/Trash", junk_fallback="[Gmail]/Spam")
_BRIDGE: dict[str, Any] = dict(bridge=True, bulk_expunge_unsafe=True, recycle_seconds=90, recycle_uses=40)


def resolve(acct, capabilities=None):
    """The profile for a config account (a dict with "kind", "host", "starttls"), with the
    server's CAPABILITY list when one is at hand."""
    kind = acct.get("kind") or "imap"
    if kind == "gmail":
        p = _GMAIL
    elif kind == "proton":
        p = ProviderProfile("proton", marks_trash_read=True, **_BRIDGE)
    elif (acct.get("host") or "").strip().lower() in _LOOPBACK and acct.get("starttls"):
        p = ProviderProfile(kind, **_BRIDGE)
    else:
        p = ProviderProfile(kind)
    return p.with_capabilities(capabilities) if capabilities is not None else p
