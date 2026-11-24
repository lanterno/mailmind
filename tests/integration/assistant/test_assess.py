"""The assess job: triage runs on a lane of its own, outside the sync's single-flight, so a slow
model never holds up a sync. A sync that brings new mail kicks it. While AI is off it tries nothing
at all. Fictional mail and the fake Claude API only."""
from fixtures import world as w
from mailmind.bootstrap import mailbox
from mailmind.bootstrap import background


DEE, EVE = "dee@example.test", "eve@example.test"            # not among the world's accounts


def added(app, world, email, subjects=()):
    """`email`'s account added, its Inbox holding a message for each of `subjects`, none synced yet."""
    entry = world.server.add_account(email)
    for n, subject in enumerate(subjects):
        world.server.deliver(email, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml", Subject=subject,
                                                       Message_ID=f"<{email}-{n}@example.test>"))
    r = app.post("/api/accounts/add", {"email": email, "type": "imap", "host": entry["host"], "port": entry["port"],
                                        "starttls": entry["starttls"],
                                        "credential": {"style": "keychain", "value": "app-password"}})
    assert r.status == 200, r.json


def test_a_first_sync_that_keeps_mail_kicks_triage_though_none_of_it_is_new(app, world, ai, monkeypatch):
    """Until its summaries come nothing knows what needs you, so an account just added doesn't wait out the interval."""
    kicked = []
    monkeypatch.setattr(background, "_kick", kicked.append)
    added(app, world, EVE)                                   # an empty Inbox: nothing to summarize
    mailbox.syncing.sync_guarded({EVE})
    assert kicked == []
    added(app, world, DEE, ("Dee's first note", "Dee's second note"))
    mailbox.syncing.sync_guarded({DEE})
    assert (kicked, mailbox.syncing._sync["result"], ai.requests) == (["assess"], "0 new", [])
    mailbox.syncing.sync_guarded({DEE})                      # nothing more: no kick
    assert kicked == ["assess"]


def test_a_sync_with_new_mail_kicks_triage_and_asks_nothing_itself(app, world, ai, monkeypatch):
    kicked = []
    monkeypatch.setattr(background, "_kick", kicked.append)
    world.server.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml",       # on the server only
                                                   Message_ID="<lunch-5@example.test>",
                                                   Subject="Re: Lunch, a table for three"))
    mailbox.syncing.sync_guarded({w.ADA})
    assert (kicked, mailbox.syncing._sync["result"], ai.requests) == (["assess"], "1 new", [])
    mailbox.syncing.sync_guarded({w.ADA})
    assert (kicked, mailbox.syncing._sync["result"]) == (["assess"], "0 new")
