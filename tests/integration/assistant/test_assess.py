"""The assess job: triage runs on a lane of its own, outside the sync's single-flight, so a slow
model never holds up a sync. A sync that brings new mail kicks it. While AI is off it tries nothing
at all. Fictional mail and the fake Claude API only."""
from fixtures import world as w
from mailmind.bootstrap import mailbox
from mailmind.bootstrap import background


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
