"""Who a message went to, one row each: what `/api/contacts/known` reads, how ingest and opening a message
write it, and what migration 35 made of the text column for mail already stored."""


from fixtures import world as w

LONG = [f"person-{i:03}@far.example.test" for i in range(150)]      # well past the 2000 characters the text column keeps


def _mail(to, cc="", mid="sent-long@example.test", sender=w.ADA):
    return (f"From: Ada <{sender}>\r\nTo: {to}\r\n" + (f"Cc: {cc}\r\n" if cc else "")
            + f"Subject: Plans\r\nMessage-ID: <{mid}>\r\nDate: Mon, 02 Nov 2026 09:00:00 +0000\r\n"
            "Content-Type: text/plain\r\n\r\nHello.\r\n").encode()


def _known(app, *addrs):
    r = app.post("/api/contacts/known", {"addrs": list(addrs)})
    return {k: v["known"] for k, v in r.json["items"].items()}


def _rows(pk):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return sorted(r[0] for r in c.execute("SELECT addr FROM message_recipients WHERE message_pk=?", (pk,)))
    finally:
        c.close()


def _pk(mid):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return c.execute("SELECT pk FROM messages WHERE message_id=?", (f"<{mid}>",)).fetchone()[0]
    finally:
        c.close()


def test_someone_past_the_old_cut_is_known(app, world):
    world.deliver(w.ADA, "Sent", _mail(", ".join(LONG)), (w.SEEN,))
    assert _known(app, LONG[0], LONG[-1], "person-150@far.example.test") == {
        LONG[0]: True, LONG[-1]: True, "person-150@far.example.test": False}


def test_to_and_cc_each_count(app, world):
    world.deliver(w.ADA, "Sent", _mail("Tam <tam@a.example.test>", '"Ng, Vi" <vi@a.example.test>'), (w.SEEN,))
    assert _rows(_pk("sent-long@example.test")) == ["tam@a.example.test", "vi@a.example.test"]


def test_a_header_given_twice_counts_twice(app, world):
    raw = _mail("one@a.example.test").replace(b"Subject:", b"To: two@a.example.test\r\nSubject:")
    world.deliver(w.ADA, "Sent", raw, (w.SEEN,))
    assert _rows(_pk("sent-long@example.test")) == ["one@a.example.test", "two@a.example.test"]


def test_a_list_folded_across_lines_is_read_whole(app, world):
    world.deliver(w.ADA, "Sent", _mail("one@a.example.test,\r\n two@a.example.test"), (w.SEEN,))
    assert _rows(_pk("sent-long@example.test")) == ["one@a.example.test", "two@a.example.test"]


def test_a_name_never_passes_for_an_address(app, world):
    for i, name in enumerate(('"ann@a.example.test via List"', "=?utf-8?Q?ann=40a.example.test?=")):
        world.deliver(w.ADA, "Sent", _mail(f"{name} <list{i}@a.example.test>", mid=f"named-{i}@example.test"), (w.SEEN,))
    known = _known(app, "ann@a.example.test", "list0@a.example.test", "list1@a.example.test")
    assert known == {"ann@a.example.test": False, "list0@a.example.test": True, "list1@a.example.test": True}


def test_a_malformed_entry_costs_only_itself(app, world):
    world.deliver(w.ADA, "Sent", _mail("Bad, <<, good@a.example.test; also@a.example.test, undisclosed-recipients:;"),
                  (w.SEEN,))
    assert {"good@a.example.test", "also@a.example.test"} <= set(_rows(_pk("sent-long@example.test")))


def test_an_address_is_kept_in_lower_case(app, world):
    world.deliver(w.ADA, "Sent", _mail("Mixed.Case@A.Example.Test"), (w.SEEN,))
    assert _known(app, "mixed.case@a.example.test") == {"mixed.case@a.example.test": True}


def test_storing_a_body_reads_its_whole_list_again(app, world):
    from mailmind.bootstrap.mailbox import store
    world.deliver(w.ADA, "Sent", _mail(", ".join(LONG)), (w.SEEN,))
    pk = _pk("sent-long@example.test")
    c = store.conn()
    store.set_recipients(c, pk, LONG[:3])       # what migration 35 could read of a column that was cut
    c.commit()
    c.close()
    assert not _known(app, LONG[-1])[LONG[-1]]
    assert app.get("/api/message/body?key={key}&folder={folder}".format(**w.copy_of(pk))).status == 200
    assert _rows(pk) == sorted(LONG)
    assert _known(app, LONG[-1])[LONG[-1]]


def test_a_copy_that_goes_takes_its_rows(app, world):
    from mailmind.bootstrap.mailbox import store
    world.deliver(w.ADA, "Sent", _mail("gone@a.example.test"), (w.SEEN,))
    pk = _pk("sent-long@example.test")
    assert _rows(pk) == ["gone@a.example.test"]
    c = store.conn()
    c.execute("DELETE FROM messages WHERE pk=?", (pk,))
    c.commit()
    c.close()
    assert _rows(pk) == []
    assert _known(app, "gone@a.example.test") == {"gone@a.example.test": False}


def test_a_forged_list_of_any_length_is_bounded(app, world, monkeypatch):
    from mailmind.mailbox.adapters.sqlite import contents
    monkeypatch.setattr(contents, "RECIPIENT_ROWS_MAX", 10)
    world.deliver(w.ADA, "INBOX", _mail(", ".join(LONG), sender="stranger@b.example.test"))
    assert len(_rows(_pk("sent-long@example.test"))) == 10


def test_mail_someone_else_sent_you_vouches_for_nobody_it_went_to(app, world):
    world.deliver(w.ADA, "INBOX", _mail("cousin@a.example.test", sender="stranger@b.example.test"))
    assert _known(app, "cousin@a.example.test") == {"cousin@a.example.test": False}


def test_a_search_reaches_the_end_of_a_long_list(app, world):
    world.deliver(w.ADA, "Sent", _mail(", ".join(LONG)), (w.SEEN,))
    pk = _pk("sent-long@example.test")
    assert w.copy_of(pk) in [{"key": m["key"], "folder": m["folder"], "uid": m["uid"]} for m in app.get(f"/api/search?q=to:{LONG[-1]}").json["items"]]


def test_the_text_kept_for_search_is_bounded(app, world, monkeypatch):
    from mailmind.bootstrap.mailbox import store
    from mailmind.mailbox.domain import ingested
    monkeypatch.setattr(ingested, "RECIPIENTS_CHARS", 100)
    world.deliver(w.ADA, "Sent", _mail(", ".join(LONG)), (w.SEEN,))
    c = store.conn()
    try:
        assert len(c.execute("SELECT recipients FROM messages WHERE pk=?", (_pk("sent-long@example.test"),)).fetchone()[0]) == 100
    finally:
        c.close()
