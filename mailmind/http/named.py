"""What a request names a message by: its key, and a copy of it by its key and folder.

A pk is a row's, and a row doesn't last: a move and its Undo, a reconcile or a new UIDVALIDITY renumber it, so a pk the page held
across one could name another message. A key is the message's (`store.mint_key`): one for each account and Message-ID, never
used again, and the same on every copy, so a folder says which copy. A route that acts on a copy (filing, a flag, the reader)
takes the one in that folder. A route that acts on the message (a summary, a reply's original, an RSVP) takes that copy where
there is one, else another. Either is looked up as the request comes, and the use case gets the row's pk of the moment.
"""
from mailmind.kernel.errors import NotFound


def copy_pk(mailbox, key, folder, gone="not found"):
    """The pk the copy `key` and `folder` name has now. Raises NotFound, saying `gone`, when the message has no copy there."""
    pk = mailbox.queries.pk_of_copy(key, folder or "")
    if pk is None:
        raise NotFound(gone)
    return pk


def message_pk(mailbox, key, folder=None, gone="not found"):
    """The pk of a copy of the message `key` names, the one in `folder` where there is one. Raises NotFound, saying `gone`,
    when no copy of it is left."""
    pk = mailbox.queries.pk_of_message(key, folder)
    if pk is None:
        raise NotFound(gone)
    return pk


def with_copy(mailbox, body, gone: str | None = "not found"):
    """A request's fields, as its use case reads them, with `pk` for the copy its `key` and `folder` name. One that names no
    key is left as it is. With `gone` None, a copy that isn't there is a `pk` of None, for the use case to word."""
    if body.get("key") is not None:
        pk = mailbox.queries.pk_of_copy(body["key"], body.get("folder") or "")
        body["pk"] = _there(pk, gone)
    return body


def with_message(mailbox, body, gone: str | None = "not found"):
    """A request's fields, as its use case reads them, with `pk` for a copy of the message its `key` names, the one in its
    `folder` where there is one. One that names no key is left as it is. With `gone` None, a message with no copy left is a
    `pk` of None, for the use case to word."""
    if body.get("key") is not None:
        body["pk"] = _there(mailbox.queries.pk_of_message(body["key"], body.get("folder")), gone)
    return body


def copies_pks(mailbox, copies):
    """The pks the copies a request names have now, leaving out any that isn't there."""
    found = (mailbox.queries.pk_of_copy(one["key"], one["folder"]) for one in copies)
    return [pk for pk in found if pk is not None]


def _there(pk, gone):
    if pk is None and gone is not None:
        raise NotFound(gone)
    return pk
