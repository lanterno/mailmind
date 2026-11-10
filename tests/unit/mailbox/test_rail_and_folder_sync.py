"""The folder rail's order and what is stored of it, and when a folder syncs (mailmind.mailbox.domain.roles and .sync_plan).
Values in and values out: test_sync_state.py, test_folder_roles.py and the routes run them against the fake."""
import pytest

from mailmind.mailbox.domain import roles, sync_plan
from mailmind.mailbox.domain.sync_plan import plan_folder


def rail(*pairs):
    return [{"name": n, "role": r, "unseen": 0, "total": 0} for n, r in pairs]


def names(folders):
    return [f["name"] for f in folders]


def test_the_rail_puts_each_part_in_its_place_and_a_persons_folders_between():
    folders = rail(("Trash", "trash"), ("Clients", "custom"), ("Junk", "junk"), ("INBOX", "inbox"), ("Sent", "sent"), ("Archive", "archive"))
    assert names(roles.rail_order(folders)) == ["INBOX", "Sent", "Archive", "Clients", "Junk", "Trash"]


def test_a_persons_folders_go_by_name_whatever_the_case():
    assert names(roles.rail_order(rail(("Zebra", "custom"), ("apple", "custom")))) == ["apple", "Zebra"]


def test_one_folder_plays_a_role_and_a_second_named_for_it_is_a_persons_own():
    folders = [roles.Folder("Sent"), roles.Folder("Old/Sent")]
    assert roles.playing(folders).get("Sent") == "sent" and "Old/Sent" not in roles.playing(folders)


def test_the_stored_rail_always_holds_the_inbox():
    assert names(roles.stored_rail({})) == ["INBOX"]


def test_the_stored_rail_gives_roles_by_name():
    by_name = {f["name"]: f["role"] for f in roles.stored_rail({"Trash": (0, 2), "Receipts": (1, 3)})}
    assert by_name == {"INBOX": "inbox", "Trash": "trash", "Receipts": "custom"}


@pytest.mark.parametrize("deeper", ["Old/Trash", "INBOX.Old.Trash"])
def test_the_stored_rail_gives_a_role_to_the_shallower_of_two(deeper):
    """A stored name keeps no delimiter: a folder deeper in the tree plays no part over one at the top, whatever its name."""
    by_name = {f["name"]: f["role"] for f in roles.stored_rail({deeper: (0, 1), "Trash": (0, 1)})}
    assert by_name["Trash"] == "trash" and by_name[deeper] == "custom"


def test_the_stored_rail_says_a_sum_it_doesnt_have_as_nothing():
    assert roles.stored_rail({"INBOX": (None, 4)}) == [{"name": "INBOX", "role": "inbox", "unseen": 0, "total": 4}]


def test_the_stored_rail_leaves_what_it_was_given_alone():
    given = {"Receipts": (1, 3)}
    roles.stored_rail(given)
    assert given == {"Receipts": (1, 3)}


@pytest.mark.parametrize("folder, reads", [("INBOX", True), ("inbox", True), ("Sent", False), ("Archive/INBOX", False)])
def test_only_the_inbox_fetches_bodies_as_it_syncs(folder, reads):
    assert sync_plan.reads_bodies(folder) is reads


def plan(**over):
    return plan_folder(**{"uidvalidity": 7, "known_uidvalidity": 7, "watermark": 40, "full": False, "recent": None, **over})


def test_mail_above_the_watermark_in_the_inbox_is_arriving():
    assert plan().arrives_in("INBOX")


@pytest.mark.parametrize("over", [{"full": True}, {"watermark": 0}, {"uidvalidity": 9}], ids=["asked", "first", "renumbered"])
def test_a_scan_reads_what_was_already_there_and_nothing_arrives(over):
    assert not plan(**over).arrives_in("INBOX")


def test_nothing_arrives_in_a_folder_but_the_inbox():
    assert not plan().arrives_in("Sent")


@pytest.mark.parametrize("asked, covers", [
    ([frozenset({"a"}), frozenset({"b"})], {"a", "b"}),
    ([frozenset({"a"}), None], None),
    ([None], None),
    ([], set()),
], ids=["accounts-add-up", "every-account-wins", "every-account", "nothing-asked"])
def test_what_the_next_round_covers(asked, covers):
    assert sync_plan.asked_again(asked) == covers


def due(folder="Sent", running=False, last_started=None, now=100.0, every=60.0):
    return sync_plan.catch_up_due(folder, running=running, last_started=last_started, now=now, every=every)


def test_a_folder_never_caught_up_is_due():
    assert due()


def test_the_inbox_never_is_since_the_background_sync_keeps_it():
    assert not due("INBOX") and not due("inbox", running=True)


def test_a_catch_up_isnt_due_again_too_soon():
    assert not due(last_started=50.0)


def test_it_is_due_once_the_time_has_passed():
    assert due(last_started=40.0)


def test_an_open_while_one_runs_waits_for_it():
    assert due(running=True, last_started=99.0)
