"""A message is one search result, however many folders hold it. Each copy has a row, and a move, a label or
Gmail's and Proton's All Mail make a second, but it is one message to the person reading the list."""
import urllib.parse

from fixtures import world as w

QUILT = "ada-inbox-11-html-cid.eml"             # Lin's quilt photo, in Ada's Inbox


def in_new_folder(world, folder, raw):
    """Deliver to a folder of Ada's that the corpus doesn't have."""
    world.server.add_folder(w.ADA, folder)
    world.deliver(w.ADA, folder, raw)


def found(app, query, **more):
    params = urllib.parse.urlencode({"q": query, "scope": "all", **more})
    answer = app.get(f"/api/search?{params}").json
    assert answer["ok"], answer
    return answer


def folders_found(app, query, **more):
    return [i["folder"] for i in found(app, query, **more)["items"]]


def zebra(n=1):
    return w.variant(QUILT, Message_ID=f"<zebra-{n}@lin.example.test>", Subject=f"Zebra {n}")


def test_a_message_in_two_folders_is_one_result_and_its_inbox_copy(app, world):
    in_new_folder(world, "Receipts", zebra())
    world.deliver(w.ADA, "INBOX", zebra())
    assert folders_found(app, "zebra") == ["INBOX"]
    assert folders_found(app, "zebra", sort="date") == ["INBOX"]


def test_a_structured_query_shows_it_once_too(app, world):
    in_new_folder(world, "Receipts", zebra())
    world.deliver(w.ADA, "INBOX", zebra())
    assert folders_found(app, "from:lin@example.test after:2020-01-01").count("Receipts") == 0
    assert folders_found(app, "in:Receipts") == ["Receipts"]


def test_all_mail_is_the_copy_shown_last(app, world):
    in_new_folder(world, "All Mail", zebra())
    in_new_folder(world, "Receipts", zebra())
    assert folders_found(app, "zebra") == ["Receipts"]


def test_all_mail_alone_is_still_found(app, world):
    in_new_folder(world, "All Mail", zebra())
    assert folders_found(app, "zebra") == ["All Mail"]


def test_a_query_about_a_folder_shows_that_folders_copy(app, world):
    in_new_folder(world, "Receipts", zebra())
    world.deliver(w.ADA, "INBOX", zebra())
    assert folders_found(app, "zebra in:Receipts") == ["Receipts"]


def test_the_same_message_id_in_two_accounts_is_two_messages(app, world):
    world.deliver(w.ADA, "INBOX", zebra())
    world.deliver(w.BEA, "INBOX", zebra())
    assert sorted(i["account"] for i in found(app, "zebra")["items"]) == sorted([w.ADA, w.BEA])


def test_a_copy_on_its_way_out_leaves_the_other_to_be_found(app, world):
    import store
    in_new_folder(world, "Receipts", zebra())
    world.deliver(w.ADA, "INBOX", zebra())
    c = store.conn()
    c.execute("UPDATE messages SET pending_removal=1 WHERE folder='INBOX' AND message_id='<zebra-1@lin.example.test>'")
    c.commit()
    c.close()
    assert folders_found(app, "zebra") == ["Receipts"]


def test_a_page_counts_messages_not_copies(app, world):
    for n in (1, 2, 3):
        in_new_folder(world, "Receipts", zebra(n))
        world.deliver(w.ADA, "INBOX", zebra(n))
    first = found(app, "zebra", limit=2, sort="date")
    second = found(app, "zebra", limit=2, offset=2, sort="date")
    assert (len(first["items"]), first["has_more"], len(second["items"]), second["has_more"]) == (2, True, 1, False)
    assert len({i["key"] for i in first["items"] + second["items"]}) == 3


def test_a_ranked_search_still_ranks(app, world):
    world.deliver(w.ADA, "INBOX", zebra(1))
    world.deliver(w.ADA, "INBOX", w.variant(QUILT, Message_ID="<body@lin.example.test>", Subject="Zebra Zebra Zebra"))
    subjects = [i["subject"] for i in found(app, "zebra")["items"]]
    assert subjects == ["Zebra Zebra Zebra", "Zebra 1"]


def test_ask_counts_a_message_once(app, world, ai):
    import store
    in_new_folder(world, "Receipts", zebra())
    world.deliver(w.ADA, "INBOX", zebra())
    c = store.conn()
    [pk] = [r["pk"] for r in c.execute("SELECT pk FROM messages WHERE folder='INBOX' AND subject='Zebra 1'")]
    store.save_summary(c, pk, "Lin wrote about a zebra.", 3, "personal", "", "claude-opus-5-5", "2026-11-02T12:00:00+00:00")
    c.commit()
    c.close()
    assert app.post("/api/ai/ask", {"account": "all", "question": "zebra"}).status == 200
    assert ai.requests[-1].prompt.count("Zebra 1") == 1
