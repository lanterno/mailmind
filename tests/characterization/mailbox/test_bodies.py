"""A message's body and part rows: a fetch the server refuses, and part rows recorded again when it opens."""
from fixtures import world as w
from stored_rows import rows
from world_helpers import late, stored


def test_hwf_a_message_whose_part_rows_were_cleared_records_them_when_opened(world):
    from mailmind.bootstrap.mailbox import ingest
    from mailmind.bootstrap.mailbox import store
    name = "ada-inbox-07-forward-rfc822.eml"
    pk = world.pk(name)
    first = ingest.fetch_body(pk)["attachments"]
    assert first
    c = store.conn()
    c.execute("DELETE FROM attachments WHERE message_pk=?", (pk,))     # what migration 28 does
    c.commit()
    c.close()
    assert ingest.fetch_body(pk)["attachments"] == first
    assert [r["part_id"] for r in rows("SELECT part_id FROM attachments WHERE message_pk=?", pk)] == [
        a["part_id"] for a in first]


def test_hwf_a_body_fetch_the_server_refuses_is_logged_and_the_row_kept(world, capsys):
    def body_fetch(args):
        items = args[1] if isinstance(args[1], list) else [args[1]]
        return [str(i).upper() for i in items] == ["BODY.PEEK[]"]
    world.server.fail("UID FETCH", user=w.ADA, when=body_fetch)
    uid = world.deliver(w.ADA, "INBOX", late())
    assert f"[ingest] body fetch <withheld> uid {uid}: NO" in capsys.readouterr().out
    assert stored()[uid] == "<late-1@example.test>"
