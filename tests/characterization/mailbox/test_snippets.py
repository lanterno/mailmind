"""The snippet a stored message shows: what HTML hides, invisible padding, and when a body or a rescan may change it."""
from fixtures import world as w
from stored_rows import col, hits, put, q, rows


def test_hwf_a_fetched_body_fills_only_an_empty_snippet(db):
    from mailmind.bootstrap.mailbox import store
    kept = put(db, "<k@example.test>", snippet="what ingest saw")
    empty = put(db, "<e@example.test>")
    for pk in (kept, empty):
        store.fts_set_body(db, pk, "The heron   came back &amp; stayed.")
    assert col(db, kept, "snippet") == "what ingest saw"
    assert col(db, empty, "snippet") == "The heron came back & stayed."
    assert col(db, kept, "body_text") == "The heron   came back &amp; stayed."
    assert hits(db, '"heron"') == [kept, empty]


def test_hwf_a_full_rescan_keeps_the_snippet_of_an_opened_message(world):
    from mailmind.bootstrap import accounts
    from mailmind.bootstrap.mailbox import ingest
    pk = world.pk("ada-sent-01-lunch-reply.eml")
    ingest.fetch_body(pk)
    before = world.row("ada-sent-01-lunch-reply.eml")["snippet"]
    assert before.startswith("Thursday works.")
    ingest.sync_folder(accounts.get(w.ADA), "Sent", full=True)        # as a draft-409 resync does to Drafts
    assert world.row("ada-sent-01-lunch-reply.eml")["snippet"] == before


def test_hwf_an_inbox_snippet_drops_invisible_preheader_padding(world):
    raw = (b"From: Noor Haddad <noor@studio.example.test>\r\nTo: ada@example.test\r\nSubject: Studio hours\r\n"
           b"Date: Mon, 02 Nov 2026 09:00:00 +0000\r\nMessage-ID: <hours-1@studio.example.test>\r\n"
           b"MIME-Version: 1.0\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
           b"<div>Doors open at nine" + b"&zwnj;&nbsp;" * 30 + b"</div><p>See you there.</p>\r\n")
    world.deliver(w.ADA, "INBOX", raw)
    [row] = rows("SELECT snippet FROM messages WHERE message_id='<hours-1@studio.example.test>'")
    assert row["snippet"].startswith("Doors open at nine")
    assert "\u200c" not in row["snippet"]


STYLED = ("From: Lin Mori <lin@example.test>\r\nTo: Ada Park <ada@example.test>\r\n"
          "Subject: Styled note\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
          "Message-ID: <styled-1@lin.example.test>\r\nMIME-Version: 1.0\r\n"
          "Content-Type: text/html; charset=utf-8\r\n\r\n"
          "<html><head><title>Hidden title</title><style>.ReadMsgBody{width:100%}</style></head>"
          "<body><script>var tracker = 1;</script><noscript>Turn on scripts</noscript>"
          "<!-- layout table --><p>Fabric is in.</p></body></html>\r\n")


def test_hwf_an_html_only_snippet_shows_no_style_script_title_or_comment(world):
    uid = world.deliver(w.ADA, "INBOX", STYLED)
    [r] = q("SELECT snippet FROM messages WHERE account=? AND folder='INBOX' AND uid=?", w.ADA, uid)
    assert r["snippet"].split() == ["Fabric", "is", "in."]
