"""What the characterization tests ask of the fictional world and its fake servers: the mail they name, one more message, where a
message is and a body to send."""
from datetime import timezone
from urllib.parse import urlencode

from fixtures import world as w
from stored_rows import q

RECEIPT = "ada-inbox-05-receipt.eml"
FOLDED = "ada-inbox-09-rfc2047-folded.eml"
LUNCH_CONFIRM = "ada-inbox-02-lunch-confirm.eml"
VENUE_END = "cy-inbox-02-thread-end.eml"
UTC = timezone.utc


def acct(email=w.ADA):
    from mailmind.bootstrap import accounts
    return accounts.get(email)


def late(n=1, **headers):
    """One more of Lin's lunch replies, with its own Message-ID."""
    return w.variant("ada-inbox-02-lunch-confirm.eml", **{"Message_ID": f"<late-{n}@example.test>",
                                                          "Subject": f"Re: Lunch, late word {n}", **headers})


def stored(folder="INBOX", email=w.ADA):
    """{uid: message_id} of the folder's rows in the database."""
    return {r["uid"]: r["message_id"] for r in q(
        "SELECT uid, message_id FROM messages WHERE account=? AND folder=?", email, folder)}


def boxes(world, name):
    """Where `name` is: [(account, folder)] on the server, and in the database."""
    got = world.where(name)
    return sorted((s[0], s[1]) for s in got["server"]), sorted((d["account"], d["folder"]) for d in got["db"])


def server_boxes(world, name):
    return sorted((s[0], s[1]) for s in world.where(name)["server"])


def gone(world, name):
    """Another client deletes `name` from the server."""
    with world.server.lock:
        world.server.mailbox(*world.placed[name][:2]).messages.pop(world.uid(name))


def count(world, command, user=w.ADA):
    return len(world.server.commands_for(user, command))


def body_of(copy, **more):
    """Where the reader opens a copy, by its key and folder."""
    return "/api/message/body?" + urlencode({**copy, **more})


def new(**kw):
    return {"account": w.ADA, "mode": "new", "to": ["Lin Mori <lin@example.test>"], "subject": "Fabric order",
            "text": "Hi Lin,\n\nThe linen came in. Want half?\n\nAda", **kw}


class _Stop(BaseException):
    """Ends one of the app's endless loops from inside a stubbed sleep."""
