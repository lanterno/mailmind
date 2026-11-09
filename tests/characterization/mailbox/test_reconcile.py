"""Reconcile: its short-lived connection, an empty answer, and mail deleted from another folder."""
import pytest

from fixtures import world as w
from world_helpers import acct, count, gone, late, stored


def test_hwf_reconcile_connects_with_a_short_timeout_and_always_logs_out(world, monkeypatch):
    from mailmind.bootstrap.mailbox import reconciler
    from mailmind.bootstrap import mailbox
    real, timeouts = mailbox.open_session, []
    monkeypatch.setattr(mailbox, "open_session", lambda a, timeout=60: (timeouts.append(timeout), real(a, timeout))[1])
    for trouble in (None, "UID SEARCH", "EXAMINE"):
        if trouble:
            world.server.fail(trouble, user=w.ADA)
        logins, logouts = count(world, "LOGIN"), count(world, "LOGOUT")
        res = reconciler.reconcile_account(acct())
        assert bool(res["error"]) == bool(trouble), (trouble, res)
        assert (count(world, "LOGIN"), count(world, "LOGOUT")) == (logins + 1, logouts + 1), trouble
    assert timeouts == [15, 15, 15]


@pytest.mark.parametrize("body", [{}, {"force": True}], ids=["plain", "whatever-it-says"])
def test_hwf_an_empty_answer_is_no_reason_to_prune_a_stored_inbox(app, world, body):
    with world.server.lock:
        world.server.mailbox(w.ADA, "INBOX").messages.clear()
    app.post("/api/reconcile", {"account": w.ADA, **body})
    assert stored() != {}                                        # an empty answer may be the server's fault


def test_hwf_mail_deleted_from_another_folder_leaves_the_list(app, world):
    name = "ada-archive-01-old-thread.eml"
    world.deliver(w.ADA, "Archive", late())                      # so the folder isn't left empty
    gone(world, name)
    r = app.post("/api/reconcile", {"account": w.ADA}).json
    assert world.where(name)["db"] == [] and r["total_pruned"] == 1
