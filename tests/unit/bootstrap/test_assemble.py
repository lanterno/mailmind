"""What wiring.assemble gives (mailmind.bootstrap.wiring): a new set of every context's objects, as a process starts with,
which each test gets before it begins and app_client.restart mid-test."""
from mailmind.attention.domain.events import TasksChanged
from mailmind.bootstrap import accounts, assistant, attention, background, calendar, compose, curation, discovery, mailbox, wiring
from mailmind.platform import bus, sse


def held():
    return [accounts.config, accounts.logins, mailbox.commands, mailbox.pool, mailbox.syncing, mailbox.undo,
            attention.snoozing, calendar.invites, compose.outbox, curation.cleanup, discovery.search, assistant.governor(),
            assistant.status, background.JOBS]


def test_every_context_gets_new_objects():
    before = held()
    wiring.assemble()
    assert [a is b for a, b in zip(before, held())] == [False] * len(before)


def test_what_front_build_chose_goes_back_to_how_a_process_starts():
    mailbox.offer("imapclient")
    wiring.assemble()
    assert mailbox.kind_for({"imap_adapter": "imapclient"}) == "imaplib"


def test_nothing_stays_subscribed_and_no_tab_is_listening():
    wiring.wire()
    hub = sse.HUB
    wiring.assemble()
    assert sse.HUB is not hub and sse.HUB.listening() == 0
    listener = sse.HUB.listen()
    try:
        bus.publish(TasksChanged())          # wired, this tells the tabs
        assert listener.next(timeout=0.2) == []
    finally:
        listener.close()
