"""Replying: to the sender, and to everyone a message named. Ported from tests/e2e/browser/test_compose.py."""
from mmtest import clients, mail


@clients("page")
def test_words_typed_in_a_reply_all_go_above_the_signature(world, app):
    world.account("ada").signature = "Ada at the Guild"
    note = world.deliver(mail.from_("lin").to(6).cc(4).subject("Venue"))

    app.inbox.show_tab("Everything")
    app.inbox.row(subject="Venue").open()
    composer = app.reader.reply_all()
    composer.type("Got it, thanks.")
    assert composer.send_button.visible
    composer.send()
    # Ten people: it asks first, and sends only when told to.
    assert composer.asks and composer.asks[0].startswith("Not sent yet · This reply goes to 10 people."), composer.asks
    assert world.smtp.sent == []
    composer.send_anyway()
    assert not composer.open
    assert [f for f in app.floats if "Sending in 10s" in f], app.floats
    world.clock.advance(seconds=11)

    sent = world.smtp.wait_for_one()
    assert sent.text.startswith("Got it, thanks.\n\n-- \nAda at the Guild")
    assert sent.recipients == note.reply_all_recipients(me="ada")


@clients("page", "mac")
def test_a_reply_answers_the_sender_and_threads(world, app):
    note = world.deliver(mail.from_("lin").subject("Lunch on Friday?").body("Shall we try the new place?"))

    app.inbox.show_tab("Everything")
    app.inbox.row(subject="Lunch on Friday?").open()
    composer = app.reader.reply()
    # The page's composer is titled "Reply"; the Mac's reply box in the reader says whom it answers. What it sends
    # says the rest, on both.
    assert composer.now.title.startswith("Reply"), composer.now.title
    assert app.shown().focus["label"] == "message body"
    composer.type("See you there.")
    composer.send()
    world.clock.advance(seconds=11)

    sent = world.smtp.wait_for_one()
    assert sent.recipients == ["lin@example.test"]
    assert sent.subject == "Re: Lunch on Friday?"
    assert sent.message["In-Reply-To"] == note.message_id
    assert sent.text.startswith("See you there.")
