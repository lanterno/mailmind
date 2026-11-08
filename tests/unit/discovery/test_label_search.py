"""`label:` in the search grammar: a Gmail message is found under every label it carries, in any case, a system one too, and a
message of any other account is found by none."""
import pytest

from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap.mailbox import ingest, store

CY = "cy-inbox-03-unrelated.eml"
ADA = "ada-inbox-05-receipt.eml"


@pytest.fixture
def labelled(world):
    """Cy's Gmail message carrying labels of its own, and arriving anew so a sync fetches it."""
    next(m for m in world.server.messages(w.CY, "INBOX").values() if (m.headers().get("Message-ID") or "").strip() == w.message_id(CY)
         ).labels |= {"Travel", "Two words"}
    c = store.conn()
    try:
        c.execute("UPDATE sync_state SET last_uid=0 WHERE account=? AND folder='INBOX'", (w.CY,))
        c.commit()
    finally:
        c.close()
    ingest.sync_new(accounts.get(w.CY))
    return world


def found(app, text):
    return {i["subject"] for i in app.get("/api/search?q=" + text.replace(" ", "%20")).json["items"]}


def test_label_finds_every_message_carrying_it(labelled, app):
    subject = labelled.row(CY)["subject"]
    assert found(app, "label:Travel") == {subject}
    assert found(app, 'label:"Two%20words"') == {subject}


def test_label_is_any_case_and_finds_a_system_label_too(labelled, app):
    subject = labelled.row(CY)["subject"]
    assert found(app, "label:travel") == {subject}
    assert subject in found(app, "label:inbox")


def test_a_label_no_message_carries_finds_nothing(labelled, app):
    assert found(app, "label:Nope") == set()


def test_negated_label_leaves_those_out(labelled, app):
    subject = labelled.row(CY)["subject"]
    assert subject not in found(app, "-label:Travel")


def test_a_non_gmail_message_is_found_by_no_label(world, app):
    assert world.row(ADA)["subject"] not in found(app, "label:Inbox")
