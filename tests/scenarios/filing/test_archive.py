"""Filing from the reader, and Undo. Ported from tests/e2e/browser/test_mailbox.py."""

RECEIPT = "ada-inbox-05-receipt.eml"


def folders(world, subject):
    return [folder for _, folder, _ in world.where(subject)]


def test_archive_files_the_open_message_opens_the_next_and_undo_brings_it_back(world, app):
    receipt = world.subject_of(RECEIPT)
    app.inbox.show_tab("Everything")
    app.inbox.row(subject=receipt).open()
    assert app.reader.subject == receipt

    app.press("e")
    now = app.shown()
    assert receipt not in now.subjects()
    assert now.open is not None and now.open["subject"] != receipt, "the next message opened"
    assert now.chosen is not None and now.chosen.subject == now.open["subject"]
    assert folders(world, receipt) == ["Archive"]
    assert any("Undo" in f for f in now.floats), now.floats

    app.undo()
    assert now.chosen.subject in app.inbox.subjects
    assert receipt in app.inbox.subjects
    assert folders(world, receipt) == ["INBOX"]
    assert "undone" in app.toast
