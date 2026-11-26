"""Triage: the Inbox's Needs you tab holds what the AI's triage says needs you, until it is dealt with. Ported from
tests/e2e/browser/test_senders.py and test_brief.py, which judge mail the same way."""
from mmtest import clients, mail

pytestmark = clients("page", "mac")


def folders(world, subject):
    return [folder for _, folder, _ in world.where(subject)]


def test_what_needs_you_waits_on_its_tab_until_it_is_filed_and_undo_puts_it_back(world, app):
    venue = world.deliver(mail.from_("lin").subject("Venue for the guild dinner"))
    world.deliver(mail.from_("mae").subject("Minutes from the last meeting"))
    world.judge(venue, importance=5, category="action-needed", action="Reply")

    app.inbox.open("ada", "INBOX")
    assert app.inbox.tab("Needs you")["selected"], "the Inbox opens on what needs you"
    assert app.inbox.count("Needs you") == 1
    assert app.inbox.subjects == ["Venue for the guild dinner"]

    app.inbox.row(subject="Venue for the guild dinner").open()
    app.press("e")
    assert app.inbox.count("Needs you") == 0
    assert app.inbox.subjects == []
    assert folders(world, "Venue for the guild dinner") == ["Archive"]
    assert folders(world, "Minutes from the last meeting") == ["INBOX"], "only what was open was filed"

    app.undo()
    assert app.inbox.count("Needs you") == 1
    assert app.inbox.subjects == ["Venue for the guild dinner"]
    assert folders(world, "Venue for the guild dinner") == ["INBOX"]


def test_what_triage_rates_low_never_reaches_needs_you(world, app):
    note = world.deliver(mail.from_("ivo").subject("Harbor newsletter, issue 9"))
    world.judge(note, importance=2, category="info", action="")

    app.inbox.open("ada", "INBOX")
    assert app.inbox.count("Needs you") == 0
    app.inbox.show_tab("Everything")
    assert "Harbor newsletter, issue 9" in app.inbox.subjects
