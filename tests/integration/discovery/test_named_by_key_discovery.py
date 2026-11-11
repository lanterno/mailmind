"""Discovery's answers name a message by key and a copy by key and folder: a file in the Document Hub, and a link to it,
and the contact card's recent mail."""
from fixtures import world as w

NAMES = [name for name, acct, folder, _, _ in w.MAIL if acct == w.ADA and folder == "INBOX"]


def test_a_file_names_its_copy_and_its_link_fetches_it_from_there(app, world):
    for name in NAMES:                                      # a body opened stores what its files are
        assert app.get("/api/message/body?key={key}&folder={folder}".format(**world.copy(name))).status == 200
    docs = app.get("/api/documents?limit=100").json["docs"]
    copies = {(c["key"], c["folder"]) for c in map(world.copy, NAMES)}
    assert docs and all((d["key"], d["folder"]) in copies for d in docs)
    for d in docs:
        assert d["url"].startswith("/api/message/attachment?key=")
        assert app.get(d["url"]).status == 200, d["url"]


def test_the_contact_card_names_recent_mail_by_its_copy(app, world):
    recent = app.get("/api/contact?addr=lin@example.test").json["recent"]
    assert recent and world.copy("ada-inbox-02-lunch-confirm.eml") in [{"key": r["key"], "folder": r["folder"], "uid": r["uid"]} for r in recent]
