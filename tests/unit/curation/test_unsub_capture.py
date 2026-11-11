"""Cleanup can offer an unsubscribe for any sender. Ingest keeps a message's unsubscribe headers, mail
from before that has them read off the server on demand, and what was read outlives the sender's mail,
so trying again after the sweep still knows the way. Where the way comes from is the use case's
(mailmind.curation.application.unsubscribe.Unsubscribing.resolve), held here on fakes behind Curation's ports."""
import json

from mailmind.content import mime
from mailmind.curation.application.unsubscribe import Unsubscribing
from mailmind.curation.domain.unsubscribe import NO_METHOD, parse_list_unsub

LOOM = "news@loom.example.test"
ADA, BEA = "ada@example.test", "bea@example.test"
ONE_CLICK = mime.list_unsub("<https://loom.example.test/u/ada-218>", "List-Unsubscribe=One-Click")
HEADERS = b"List-Unsubscribe: <https://loom.example.test/u/ada-218>\r\nList-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n\r\n"
NEWEST, OLDER = {"pk": 9, "account": ADA, "folder": "INBOX", "uid": 19}, {"pk": 4, "account": ADA, "folder": "INBOX", "uid": 14}


def test_a_url_folded_in_the_middle_comes_back_whole():
    # RFC 2369 says whitespace within one is ignored; a space or a line break left in it can't be requested.
    r = parse_list_unsub(b"List-Unsubscribe: <https://list.example.test/u?t=abc\r\n def>,\r\n"
                                 b" <mailto:out@list.example.test?subject=\r\n stop>\r\n\r\nHello.\r\n")
    assert (r["https_uri"], r["mailto_to"], r["mailto_subject"]) == (
        "https://list.example.test/u?t=abcdef", "out@list.example.test", "stop")


# ── where the way out comes from ─────────────────────────────────────────────────────────────────

class World:
    """The sender's stored mail (SenderMail) and the server's word on one message's headers (ListHeaders), each asked noted."""

    def __init__(self, rows=(NEWEST, OLDER), stored=None, kept=None, heard=None):
        self.rows, self.stored, self.kept, self.heard = list(rows), stored, kept, heard
        self.said = []

    def unsub_stored(self, from_addr):
        return self.rows, [ADA], self.stored

    def remember_unsub(self, pk, parsed):
        self.said.append(("remember", pk, parsed["method"]))

    def keep_unsub(self, from_addr, info, accounts):
        self.said.append(("keep", from_addr, info["method"], accounts))

    def kept_unsub(self, from_addr):
        return self.kept

    def read(self, row):
        self.said.append(("read", row["pk"]))
        return self.heard

    def resolve(self):
        return Unsubscribing(self, self, None, None).resolve(LOOM)


def test_what_ingest_kept_answers_and_the_server_isnt_asked():
    world = World(stored=ONE_CLICK)
    assert world.resolve() == (ONE_CLICK, [ADA])
    assert world.said == [("keep", LOOM, "one-click", [ADA])]


def test_mail_from_before_ingest_kept_them_has_the_newest_read_off_the_server_and_remembered():
    world = World(heard=HEADERS)
    info, accounts = world.resolve()
    assert (info["method"], info["https_uri"], accounts) == ("one-click", "https://loom.example.test/u/ada-218", [ADA])
    assert world.said == [("read", 9), ("remember", 9, "one-click"), ("keep", LOOM, "one-click", [ADA])]


def test_with_the_server_unable_to_say_no_way_is_offered_and_nothing_is_remembered():
    world = World(heard=None)
    assert world.resolve() == (NO_METHOD, [ADA])
    assert world.said == [("read", 9)]


def test_with_none_of_the_senders_mail_left_what_was_last_read_still_knows_the_way():
    world = World(rows=[], kept={"raw": ONE_CLICK["raw"], "accounts": json.dumps([BEA])})
    assert world.resolve() == (ONE_CLICK, [BEA])
    assert world.said == []


def test_a_newest_message_offering_no_way_is_taken_at_its_word_over_what_was_kept():
    world = World(stored=NO_METHOD, kept={"raw": ONE_CLICK["raw"], "accounts": json.dumps([ADA])})
    assert world.resolve() == (NO_METHOD, [ADA])
    assert world.said == [("keep", LOOM, "none", [ADA])]
