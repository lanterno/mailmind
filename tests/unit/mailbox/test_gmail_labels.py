"""Gmail's labels, read at sync and kept on each copy: a Gmail message found under every label it carries, and nothing changed for
any other account."""
import pytest

from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.mailbox import ingest, store

CY = "cy-inbox-03-unrelated.eml"
ADA = "ada-inbox-05-receipt.eml"


def q(sql, *params):
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, params)]
    finally:
        c.close()


def labels_of(pk):
    return {r["label"] for r in q("SELECT label FROM message_labels WHERE message_pk=?", pk)}


def fake_message(world, name, user=w.CY, box="INBOX"):
    return next(m for m in world.server.messages(user, box).values() if (m.headers().get("Message-ID") or "").strip() == w.message_id(name))


@pytest.fixture
def labelled(world):
    """Cy's Gmail message carrying labels of its own, and arriving anew so a sync fetches it."""
    msg = fake_message(world, CY)
    msg.labels |= {"Travel", "Two words"}
    c = store.conn()
    try:
        c.execute("UPDATE sync_state SET last_uid=0 WHERE account=? AND folder='INBOX'", (w.CY,))
        c.commit()
    finally:
        c.close()
    ingest.sync_new(accounts.get(w.CY))
    return world


def test_a_sync_reads_gmails_labels_for_each_copy_it_fetches(labelled):
    assert labels_of(labelled.pk(CY)) == {"Travel", "Two words", "\\Inbox"}


def test_a_sync_of_a_non_gmail_account_asks_for_none_and_keeps_none(world):
    since = len(world.server.commands)
    c = store.conn()
    try:
        c.execute("UPDATE sync_state SET last_uid=0 WHERE account=? AND folder='INBOX'", (w.ADA,))
        c.commit()
    finally:
        c.close()
    ingest.sync_new(accounts.get(w.ADA))
    assert not any("X-GM-LABELS" in str(line) for line in world.server.commands[since:])
    assert q("SELECT * FROM message_labels WHERE message_pk=?", world.pk(ADA)) == []
    assert q("SELECT count(*) AS n FROM message_labels ml JOIN messages m ON m.pk=ml.message_pk WHERE m.account=?", w.ADA)[0]["n"] == 0


def test_a_label_gone_at_the_next_fetch_goes_here_too(labelled):
    msg = fake_message(labelled, CY)
    msg.labels.discard("Travel")
    c = store.conn()
    try:
        c.execute("UPDATE sync_state SET last_uid=0 WHERE account=? AND folder='INBOX'", (w.CY,))
        c.commit()
    finally:
        c.close()
    ingest.sync_new(accounts.get(w.CY))
    assert labels_of(labelled.pk(CY)) == {"Two words", "\\Inbox"}


def test_labels_go_with_the_copy(labelled):
    pk = labelled.pk(CY)
    c = store.conn()
    try:
        c.execute("DELETE FROM messages WHERE pk=?", (pk,))
        c.commit()
        assert c.execute("SELECT count(*) FROM message_labels WHERE message_pk=?", (pk,)).fetchone()[0] == 0
    finally:
        c.close()


def test_a_flag_refresh_leaves_labels_alone(labelled):
    # a sync that reads no new mail reads flags again, and a label is not one of them
    fake_message(labelled, CY).labels.add("Added on Gmail")
    before = labels_of(labelled.pk(CY))
    ingest.sync_new(accounts.get(w.CY))
    assert labels_of(labelled.pk(CY)) == before


def test_the_page_is_told_an_accounts_own_labels_and_how_many_carry_each(labelled, app):
    got = app.get(f"/api/labels?account={w.CY}").json
    assert got == {"ok": True, "labels": [{"name": "Travel", "count": 1}, {"name": "Two words", "count": 1}]}


def test_a_non_gmail_account_has_no_labels_to_list(app):
    assert app.get(f"/api/labels?account={w.ADA}").json == {"ok": True, "labels": []}


def test_the_labels_route_needs_an_account(app):
    r = app.get("/api/labels")
    assert (r.status, r.json["detail"]) == (400, "account required")


def test_a_message_counts_once_however_many_copies_it_has(labelled, app):
    other = q("SELECT message_key FROM messages WHERE pk=?", labelled.pk(CY))[0]["message_key"]
    c = store.conn()
    try:
        pk = c.execute("INSERT INTO messages(account, kind, folder, uid, message_id, message_key) VALUES (?,?,?,?,?,?)",
                       (w.CY, "gmail", "[Gmail]/All Mail", 999, w.message_id(CY), other)).lastrowid
        c.execute("INSERT INTO message_labels(message_pk, label) VALUES (?, 'Travel')", (pk,))
        c.commit()
    finally:
        c.close()
    got = {l["name"]: l["count"] for l in app.get(f"/api/labels?account={w.CY}").json["labels"]}
    assert got["Travel"] == 1
