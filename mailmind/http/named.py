"""What a request names a message by: its key, and a copy of it by its key, its folder and its uid there.

A pk is a row's, and a row doesn't last: a move and its Undo, a reconcile or a new UIDVALIDITY renumber it, so a pk the page held
across one could name another message. A key is the message's (`store.mint_key`): one for each account and Message-ID, never
used again, and the same on every copy, so a folder says which copy. Two copies in one folder can share a key (a sender who
reuses a Message-ID, a list's copy beside the one sent straight), so the server's uid says which of them: `UNIQUE(account,
folder, uid)`, and a renumber of pks leaves it be. A route that acts on a copy (filing, a flag, the reader) takes the one at that
uid, and without a uid the only copy in that folder, never a guess between two. A route that acts on the message (a summary, a
reply's original, an RSVP) takes that copy where there is one, else another. Either is looked up as the request comes, and the
use case gets the row's pk of the moment.
"""
from mailmind.kernel.errors import NotFound


def copy_pk(mailbox, key, folder, uid=None, gone="not found"):
    """The pk the copy `key`, `folder` and `uid` name has now. Raises NotFound, saying `gone`, when that copy isn't there."""
    pk = mailbox.queries.pk_of_copy(key, folder or "", uid)
    if pk is None:
        raise NotFound(gone)
    return pk


def with_copy(mailbox, body, gone: str | None = "not found"):
    """A request's fields, as its use case reads them, with `pk` for the copy its `key`, `folder` and `uid` name: None where it
    names no key, for the use case to word. With `gone` None, a copy that isn't there is a `pk` of None too."""
    body["pk"] = None if body.get("key") is None else _there(
        mailbox.queries.pk_of_copy(body["key"], body.get("folder") or "", body.get("uid")), gone)
    return body


def with_message(mailbox, body, gone: str | None = "not found"):
    """A request's fields, as its use case reads them, with `pk` for a copy of the message its `key` names, the one at its
    `uid` in its `folder`, else in its `folder`, where there is one: None where it names no key, for the use case to word. With
    `gone` None, a message with no copy left is a `pk` of None too."""
    body["pk"] = None if body.get("key") is None else _there(
        mailbox.queries.pk_of_message(body["key"], body.get("folder"), body.get("uid")), gone)
    return body


def copies_pks(mailbox, copies):
    """The pks the copies a request names have now, leaving out any that isn't there."""
    found = (mailbox.queries.pk_of_copy(one["key"], one["folder"], one.get("uid")) for one in copies)
    return [pk for pk in found if pk is not None]


def _there(pk, gone):
    if pk is None and gone is not None:
        raise NotFound(gone)
    return pk
