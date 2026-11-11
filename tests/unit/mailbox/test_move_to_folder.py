"""Moving mail to a folder a person picked: one message, a conversation or the marked rows, through the one command path, with
Undo, and only into the account's own folders that take filing. All addresses are fictional."""
import pytest

from mailmind.mailbox.domain import destination


# ── what the domain says ─────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role, takes", [("inbox", True), ("archive", True), ("trash", True), ("junk", True), ("custom", True),
                                         ("drafts", False), ("sent", False), ("all", False)])
def test_which_parts_a_folder_plays_take_filing(role, takes):
    assert destination.takes_filing(role) is takes
