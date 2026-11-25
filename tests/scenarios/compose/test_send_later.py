"""Send Later: a message held for a time, in the outbox tray until then. Ported from tests/e2e/browser/test_compose.py."""


def scheduled(app):
    composer = app.compose()
    composer.to("lin")
    composer.subject("Fabric order")
    composer.write("Hi Lin, the linen came in.")
    composer.send_later("Tomorrow 8 AM")
    return composer


def test_a_message_sent_later_waits_in_the_tray_and_goes_at_its_time(world, app):
    composer = scheduled(app)
    assert not composer.open
    assert any("Scheduled" in f for f in app.floats), app.floats
    app.tray.open()
    assert any("Fabric order" in line and "scheduled" in line for line in app.tray.lines), app.tray.lines

    world.clock.advance(hours=19, minutes=59)              # Tuesday, 07:59
    world.run("outbox")
    assert world.smtp.sent == []

    world.clock.advance(minutes=2)
    sent = world.smtp.wait_for_one()
    assert sent.subject == "Fabric order"
    assert sent.recipients == ["lin@example.test"]
    assert sent.text.startswith("Hi Lin, the linen came in.")


def test_a_send_cancelled_in_the_tray_never_goes(world, app):
    scheduled(app)
    app.tray.open()
    app.tray.cancel("Fabric order")
    assert "Send cancelled" in app.toast
    assert not any("Fabric order" in line for line in app.tray.lines or [])

    world.clock.advance(days=1)
    world.run("outbox")
    assert world.smtp.sent == []
