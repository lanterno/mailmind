"""What the app's own first sync makes of the fictional corpus: rows, buckets, threads, flags,
decoded headers, the bodies it keeps, and what opening a message records.

These pin today's behaviour. Where today's behaviour is a bug, the test states the right
behaviour and is marked xfail(strict=True): fixing the bug makes it pass, and strict makes that
an error until the marker is removed.
"""
import pytest

from fixtures import world as w

BUCKETS = {
    "ada-inbox-01-lunch.eml": "other/personal-ish",
    "ada-inbox-02-lunch-confirm.eml": "other/personal-ish",
    "ada-inbox-03-newsletter.eml": "newsletters/marketing",
    "ada-inbox-04-ci.eml": "automated-notifications",
    "ada-inbox-05-receipt.eml": "receipts/transactional",
    "ada-inbox-06-outlook-invite.eml": "other/personal-ish",
    "ada-inbox-07-forward-rfc822.eml": "other/personal-ish",
    "ada-inbox-08-boundaryless.eml": "other/personal-ish",
    "ada-inbox-09-rfc2047-folded.eml": "other/personal-ish",
    "ada-inbox-10-raw-utf8.eml": "other/personal-ish",
    "ada-inbox-11-html-cid.eml": "other/personal-ish",
    "ada-inbox-12-scam.eml": "spam/scam",
    "ada-sent-01-lunch-reply.eml": "other/personal-ish",
    "ada-drafts-01-thunderbird-v1.eml": "other/personal-ish",
    "ada-drafts-02-thunderbird-v2.eml": "other/personal-ish",
    "ada-archive-01-old-thread.eml": "other/personal-ish",
    "ada-trash-01-promo.eml": "newsletters/marketing",
    "bea-inbox-01-client.eml": "other/personal-ish",
    "bea-inbox-02-security.eml": "personal/protected",
    "bea-clients-01-harbor.eml": "other/personal-ish",
    "bea-drafts-01-apple-mail.eml": "other/personal-ish",
    "cy-inbox-01-thread-start.eml": "other/personal-ish",
    "cy-sent-01-thread-reply.eml": "other/personal-ish",
    "cy-inbox-02-thread-end.eml": "other/personal-ish",
    "cy-inbox-03-unrelated.eml": "receipts/transactional",
}
JUNK = {"ada-inbox-03-newsletter.eml", "ada-inbox-04-ci.eml", "ada-inbox-12-scam.eml", "ada-trash-01-promo.eml"}


def _rows(sql, *args):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def test_every_message_is_stored_junk_included(world):
    stored = _rows("SELECT account, folder, count(*) AS n FROM messages GROUP BY account, folder")
    placed = {}
    for _, acct, folder, _, _ in w.MAIL:
        placed[(acct, folder)] = placed.get((acct, folder), 0) + 1
    assert {(r["account"], r["folder"]): r["n"] for r in stored} == placed
    assert [name for name, *_ in w.MAIL if world.pk(name) is None] == []


def test_each_message_lands_in_its_bucket(world):
    assert {name: world.row(name)["bucket"] for name, *_ in w.MAIL} == BUCKETS
    assert {name for name, *_ in w.MAIL if world.row(name)["is_junk"]} == JUNK


def test_flags_mirror_the_server(world):
    for name, _, _, flags, _ in w.MAIL:
        row = world.row(name)
        got = {f for f, col in (("\\Seen", "seen"), ("\\Answered", "answered"),
                                ("\\Flagged", "flagged"), ("\\Draft", "draft")) if row[col]}
        assert got == set(flags), name


def test_replies_join_their_conversation_across_folders(world):
    def tid(name):
        return world.row(name)["thread_id"]
    lunch = {tid("ada-inbox-01-lunch.eml"), tid("ada-inbox-02-lunch-confirm.eml"), tid("ada-sent-01-lunch-reply.eml")}
    assert len(lunch) == 1 and lunch.pop().startswith("rf:ada@example.test:")
    assert tid("bea-drafts-01-apple-mail.eml") == tid("bea-inbox-01-client.eml")
    assert tid("ada-drafts-01-thunderbird-v1.eml") == tid("ada-drafts-02-thunderbird-v2.eml")
    assert tid("ada-archive-01-old-thread.eml") not in {tid("ada-inbox-01-lunch.eml")}


def test_gmail_threads_follow_x_gm_thrid(world):
    venue = ["cy-inbox-01-thread-start.eml", "cy-sent-01-thread-reply.eml", "cy-inbox-02-thread-end.eml"]
    rows = [world.row(n) for n in venue]
    assert len({r["gmail_thread"] for r in rows}) == 1
    assert {r["thread_id"] for r in rows} == {"gm:cy@example.test:" + rows[0]["gmail_thread"]}
    assert world.row("cy-inbox-03-unrelated.eml")["thread_id"] != rows[0]["thread_id"]
    assert all(world.row(n)["gmail_thread"] is None for n, acct, *_ in w.MAIL if acct != w.CY)


def test_encoded_and_folded_headers_are_decoded(world):
    row = world.row("ada-inbox-09-rfc2047-folded.eml")
    assert row["subject"] == "Projet d’atelier — planning de novembre"
    assert (row["from_name"], row["from_addr"]) == ("Renée Lefèvre", "renee@atelier.example.test")
    assert world.row("ada-inbox-10-raw-utf8.eml")["subject"] == "Menu d’août — nouveautés ☕"


@pytest.mark.xfail(strict=True, reason="known bug: a raw UTF-8 display name empties the sender")
def test_a_raw_utf8_sender_keeps_its_name_and_address(world):
    row = world.row("ada-inbox-10-raw-utf8.eml")
    assert (row["from_name"], row["from_addr"]) == ("Café Lumière", "hello@cafe.example.test")


def test_inbox_mail_gets_a_snippet_and_junk_does_not(world):
    for name, acct, folder, _, _ in w.MAIL:
        row = world.row(name)
        if folder != "INBOX" or name in JUNK:
            assert row["snippet"] == "", name
    assert world.row("bea-inbox-01-client.eml")["snippet"] == "Bea, the second draft is ready for your comments."
    assert world.row("ada-inbox-11-html-cid.eml")["snippet"] == "It's done!"     # from the HTML part


@pytest.mark.xfail(strict=True, reason="known bug: snippets keep the wire's carriage returns")
def test_snippets_hold_no_carriage_returns(world):
    assert "\r" not in world.row("ada-inbox-01-lunch.eml")["snippet"]


@pytest.mark.xfail(strict=True, reason="known bug: a multipart with no boundary loses its text")
def test_a_multipart_with_no_boundary_keeps_its_text(world):
    import ingest
    assert "statement for October" in world.row("ada-inbox-08-boundaryless.eml")["snippet"]
    assert "statement for October" in (ingest.fetch_body(world.pk("ada-inbox-08-boundaryless.eml"))["text"] or "")


def test_the_first_sync_keeps_only_invite_bodies(world):
    pk = world.pk("ada-inbox-06-outlook-invite.eml")
    assert [r["message_pk"] for r in _rows("SELECT message_pk FROM bodies")] == [pk]
    assert _rows("SELECT message_pk, filename, content_type, is_inline FROM attachments") == [
        {"message_pk": pk, "filename": "invite.ics", "content_type": "text/calendar", "is_inline": 1}]
    assert world.row("ada-inbox-06-outlook-invite.eml")["has_attach"] == 1


def test_sync_state_records_each_folder_at_its_highest_uid(world):
    state = {(r["account"], r["folder"]): r for r in _rows("SELECT * FROM sync_state")}
    assert set(state) == set(w.synced_folders())
    for (acct, folder), r in state.items():
        box = world.server.mailbox(acct, folder)
        assert (r["last_uid"], r["uidvalidity"]) == (max(box.messages), box.uidvalidity), (acct, folder)


def test_opening_a_message_records_its_attachments(world):
    import ingest
    apple = ingest.fetch_body(world.pk("bea-drafts-01-apple-mail.eml"))
    assert apple["text"].startswith("Hi Ivo, my comments are in the attached file.")
    assert [(a["filename"], a["content_type"], a["size"], a["is_inline"]) for a in apple["attachments"]] == [
        ("comments.pdf", "application/pdf", 45, 0)]
    cid = ingest.fetch_body(world.pk("ada-inbox-11-html-cid.eml"))
    assert cid["text"] is None and "cid:quilt@lin.example.test" in cid["html"]
    assert [(a["filename"], a["content_id"], a["is_inline"]) for a in cid["attachments"]] == [
        ("quilt.png", "quilt@lin.example.test", 1)]
    fwd = ingest.fetch_body(world.pk("ada-inbox-07-forward-rfc822.eml"))
    assert fwd["text"] == "Forwarding Lin's note, see attached.\r\n"
    assert [(a["filename"], a["content_type"]) for a in fwd["attachments"]] == [("Fabric samples.eml", "message/rfc822")]
    assert world.row("ada-inbox-07-forward-rfc822.eml")["has_attach"] == 1
    assert world.row("ada-inbox-07-forward-rfc822.eml")["body_fetched"] == 1


@pytest.mark.xfail(strict=True, reason="known bug: a forwarded .eml attachment is empty")
def test_a_forwarded_message_attachment_carries_the_message(world):
    import mailparse
    raw = w.raw("ada-inbox-07-forward-rfc822.eml")
    att = mailparse.parse(raw)["attachments"][0]
    data, ctype = mailparse.extract_part(raw, att["part_id"])
    assert ctype == "message/rfc822" and b"Subject: Fabric samples" in data and att["size"] == len(data)


def test_opening_a_message_twice_reads_the_server_once(world):
    import ingest
    pk = world.pk("bea-inbox-01-client.eml")
    ingest.fetch_body(pk)
    before = len(world.server.commands_for(w.BEA, "UID FETCH"))
    assert ingest.fetch_body(pk)["text"].startswith("Bea, the second draft")
    assert len(world.server.commands_for(w.BEA, "UID FETCH")) == before
