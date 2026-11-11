"""A block from the reader names the message it trashes by key and folder, which still names that copy after a renumber."""
from fixtures import world as w
from mailmind.bootstrap import accounts
from mailmind.bootstrap import mailbox

NEWSLETTER = "ada-inbox-03-newsletter.eml"


def test_a_block_trashes_the_copy_its_key_and_folder_name(app, world):
    sender = world.row(NEWSLETTER)["from_addr"]
    world.server.renumber(w.ADA, "INBOX")
    mailbox.ingest.sync_new(accounts.get(w.ADA))
    r = app.post("/api/rules", {"token": sender, "verdict": "block", **world.copy(NEWSLETTER)}).json
    assert r["trashed"] is True
    assert [box for _, box, _, _ in world.where(NEWSLETTER)["server"]] == ["Trash"]


def test_a_block_naming_a_copy_that_has_gone_still_blocks_and_trashes_nothing(app, world):
    sender = world.row(NEWSLETTER)["from_addr"]
    r = app.post("/api/rules", {"token": sender, "verdict": "block", "key": world.key(NEWSLETTER), "folder": "Archive"}).json
    assert (r["ok"], r["trashed"]) == (True, False)
    assert [box for _, box, _, _ in world.where(NEWSLETTER)["server"]] == ["INBOX"]
