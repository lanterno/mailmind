"""What the model reads of a conversation: the first message and the last 19, each trimmed and within the caps. A Mail of
our own stands in for the Mailbox, so nothing here can reach a server: a body that hasn't come down is only ever asked of
the port, at interactive priority."""
from mailmind.assistant.application import context as ctx
from mailmind.assistant.application.context import ContextAssembler, lone_key
from mailmind.platform.workers import gates

ADA = "ada@example.test"


def row(n, body="A line.", **over):
    return {"pk": n, "account": ADA, "message_id": f"<m{n}@example.test>", "body_text": body, "snippet": "", "body_fetched": 1,
            "from_name": f"Sender {n}", "from_addr": f"s{n}@example.test", "date_iso": f"2026-10-{n % 28 + 1:02d}", **over}


class Mail:
    def __init__(self, rows=(), bodies=None):
        self.rows, self.bodies, self.asked, self.fetched = list(rows), bodies or {}, [], []

    def conversation_rows(self, c, account, by, value):
        self.asked.append((account, by, value))
        return self.rows

    def fetch_body(self, pk):
        self.fetched.append(pk)
        body = self.bodies[pk]
        if isinstance(body, Exception):
            raise body
        return body


def senders(blocks):
    return [b.split("\n")[0].split(" <")[0].removeprefix("--- ").split("] ")[1] for b in blocks]


def test_a_long_thread_keeps_the_first_message_and_the_last_nineteen():
    rows = [row(n) for n in range(1, 26)]
    blocks, omitted = ContextAssembler(Mail()).gather(rows)
    assert omitted == 5
    assert senders(blocks) == ["Sender 1", *[f"Sender {n}" for n in range(7, 26)]]
    assert [b.split("]")[0] for b in blocks][:2] == ["--- [1", "--- [2"]      # numbered as shown, not as they came


def test_a_thread_at_the_cap_keeps_every_message():
    blocks, omitted = ContextAssembler(Mail()).gather([row(n) for n in range(1, ctx.CAP_MSGS + 1)])
    assert (len(blocks), omitted) == (ctx.CAP_MSGS, 0)


def test_a_message_leaves_out_its_quotes_and_stops_at_what_it_quotes():
    body = "See you then.\n> an old line\n  > another\nBring the table.\nOn Tue, Bea wrote:\nwhat she wrote"
    [block], _ = ContextAssembler(Mail()).gather([row(1, body)])
    assert block.split("\n", 1)[1] == "See you then.\nBring the table."


def test_a_forward_ends_a_message_as_a_quote_does():
    [block], _ = ContextAssembler(Mail()).gather([row(1, "Have a look.\n---- Forwarded message\nFrom: someone")])
    assert block.split("\n", 1)[1] == "Have a look."


def test_a_message_is_held_to_its_cap():
    [block], _ = ContextAssembler(Mail()).gather([row(1, "x" * (ctx.CAP_PER_MSG + 50))])
    assert block.split("\n", 1)[1] == "x" * ctx.CAP_PER_MSG


def test_the_whole_is_held_to_its_cap_and_stops_there():
    rows = [row(n, "y" * ctx.CAP_PER_MSG) for n in range(1, ctx.CAP_MSGS + 1)]
    blocks, _ = ContextAssembler(Mail()).gather(rows)
    assert sum(len(b.split("\n", 1)[1]) for b in blocks) == ctx.CAP_TOTAL
    assert len(blocks) < len(rows)


def test_a_header_says_someone_when_a_message_names_no_one():
    [block], _ = ContextAssembler(Mail()).gather([row(1, from_name=None, from_addr=None, date_iso=None)])
    assert block.split("\n")[0] == "--- [1] someone <> — "


def test_a_body_at_hand_asks_the_mailbox_for_nothing():
    mail = Mail()
    assert ContextAssembler(mail).gather([row(1), row(2, None, snippet="A snippet.")])[0][1].endswith("A snippet.")
    assert mail.fetched == []


def test_a_body_already_fetched_and_empty_is_not_asked_for_again():
    mail = Mail()
    ContextAssembler(mail).gather([row(1, None)])
    assert mail.fetched == []


def test_a_body_that_has_not_come_down_is_asked_of_the_mailbox_at_interactive_priority(monkeypatch):
    runs, real = [], gates.run

    def run(account, fn, **kw):
        runs.append((account, kw["priority"]))
        return real(account, fn, **kw)
    monkeypatch.setattr(gates, "run", run)
    mail = Mail(bodies={1: {"text": "Fetched words.\n> quoted"}})
    [block], _ = ContextAssembler(mail).gather([row(1, None, body_fetched=0)])
    assert mail.fetched == [1]
    assert runs == [(ADA, gates.Priority.INTERACTIVE)]
    assert block.endswith("\nFetched words.")


def test_a_body_that_cant_be_had_reads_as_empty():
    for body in (None, OSError("no server")):
        [block], _ = ContextAssembler(Mail(bodies={1: body})).gather([row(1, None, body_fetched=0)])
        assert block.endswith("\n")


def test_a_message_counts_once_however_many_folders_hold_it():
    mail = Mail([row(1), {**row(1), "pk": 9}, row(2)])
    assert [r["pk"] for r in ContextAssembler(mail).rows(None, ADA, "t-1")] == [1, 2]
    assert mail.asked == [(ADA, "thread_id", "t-1")]


def test_a_message_with_no_id_counts_by_its_row():
    mail = Mail([row(1, message_id=None), row(2, message_id="  ")])
    assert [r["pk"] for r in ContextAssembler(mail).rows(None, ADA, "t-1")] == [1, 2]


def test_a_lone_message_is_asked_for_by_what_it_goes_by():
    assert lone_key(row(3)) == "m:<m3@example.test>"
    assert lone_key(row(3, message_id=None)) == "pk:3"
    mail = Mail()
    assembler = ContextAssembler(mail)
    assembler.rows(None, ADA, lone_key(row(3)))
    assembler.rows(None, ADA, lone_key(row(3, message_id=None)))
    assert mail.asked == [(ADA, "message_id", "<m3@example.test>"), (ADA, "pk", "3")]
