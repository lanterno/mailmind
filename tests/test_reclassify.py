"""A rule change re-classifies the stored mail it covers (decision D11), as mail arriving now would
be, and leaves alone what no rule ever had a say in."""

from fixtures import world as w

NEWS = "ada-inbox-03-newsletter.eml"
LUNCH = "ada-inbox-02-lunch-confirm.eml"
RECEIPT = "ada-inbox-05-receipt.eml"


def stored(world, name, folder="INBOX"):
    import store
    c = store.conn()
    try:
        return c.execute("SELECT * FROM messages WHERE account=? AND folder=? AND message_id=?",
                         (w.ADA, folder, w.message_id(name))).fetchone()
    finally:
        c.close()


def rule(app, token, verdict):
    r = app.post("/api/rules", {"token": token, "verdict": verdict}).json
    assert r["ok"]
    return r


def sender(world, name):
    return world.row(name)["from_addr"].lower()


def domain(world, name):
    return sender(world, name).rpartition("@")[2]


def test_blocking_a_sender_marks_what_is_stored_as_blocked(app, world):
    addr = sender(world, LUNCH)
    assert stored(world, LUNCH)["is_junk"] == 0
    r = rule(app, addr, "block")
    row = stored(world, LUNCH)
    assert r["reclassified"] >= 1
    assert (row["bucket"], row["is_junk"], row["confidence"]) == ("blocked", 1, "high")
    assert row["reason"] == "blocked by rule (sender)"


def test_it_leaves_other_senders_be(app, world):
    before = dict(stored(world, RECEIPT))
    rule(app, sender(world, LUNCH), "block")
    assert dict(stored(world, RECEIPT)) == before


def test_keeping_a_sender_brings_stored_mail_out_of_junk(app, world):
    addr = sender(world, NEWS)
    assert stored(world, NEWS)["is_junk"] == 1
    rule(app, addr, "keep")
    row = stored(world, NEWS)
    assert (row["bucket"], row["is_junk"]) == ("personal/protected", 0)


def test_mail_brought_out_of_junk_is_offered_for_triage(app, world):
    import store
    rule(app, sender(world, NEWS), "keep")
    pk = stored(world, NEWS)["pk"]
    c = store.conn()
    offered = [r["pk"] for r in store.needs_summary(c, 1000)]
    c.close()
    assert pk in offered


def test_removing_a_rule_gives_the_mail_back_to_the_classifier(app, world):
    addr = sender(world, LUNCH)
    before = stored(world, LUNCH)["bucket"]
    rule(app, addr, "block")
    rule(app, addr, "remove")
    row = stored(world, LUNCH)
    assert row["is_junk"] == 0 and row["bucket"] == before and "by rule" not in row["reason"]


def test_a_rule_that_changes_nothing_counts_nothing(app, world):
    addr = sender(world, LUNCH)
    assert rule(app, addr, "block")["reclassified"] >= 1
    assert rule(app, addr, "block")["reclassified"] == 0
    assert rule(app, "nobody@nowhere.example.test", "keep")["reclassified"] == 0


def test_mail_no_rule_had_a_say_in_stays_as_ingest_judged_it(app, world):
    # The classifier read List-Unsubscribe at ingest and that isn't kept, so asking it again now
    # would call this newsletter something else. Removing a rule it never had covers it.
    before = dict(stored(world, NEWS))
    assert before["reason"] == "List-Unsubscribe header"
    assert rule(app, sender(world, NEWS), "remove")["reclassified"] == 0
    assert rule(app, domain(world, NEWS), "remove")["reclassified"] == 0
    assert dict(stored(world, NEWS)) == before


def test_a_domain_rule_covers_its_sub_domains_and_no_lookalike(app, world):
    import store
    addr = sender(world, LUNCH)
    dom = domain(world, LUNCH)
    c = store.conn()
    pk = stored(world, LUNCH)["pk"]
    row = dict(c.execute("SELECT * FROM messages WHERE pk=?", (pk,)).fetchone())
    made = {}
    for label, from_addr in (("sub", f"a@mail.{dom}"), ("like", f"a@not{dom}"), ("wild", f"a@{dom.replace('.', 'x')}")):
        other = {k: v for k, v in row.items() if k not in ("pk", "message_key")}
        other.update(uid=row["uid"] + len(made) + 900, message_id=f"<{label}@example.test>", from_addr=from_addr)
        c.execute(f"INSERT INTO messages({','.join(other)}) VALUES({','.join('?' * len(other))})", tuple(other.values()))
        made[label] = other["message_id"]
    c.commit()
    c.close()
    rule(app, dom, "block")

    def junk(label):
        c = store.conn()
        try:
            return c.execute("SELECT is_junk FROM messages WHERE message_id=?", (made[label],)).fetchone()["is_junk"]
        finally:
            c.close()
    assert stored(world, LUNCH)["reason"] == "blocked by rule (domain)" and addr
    assert junk("sub") == 1
    assert junk("like") == 0 and junk("wild") == 0


def test_a_kept_sender_stays_kept_under_a_blocked_domain(app, world):
    rule(app, sender(world, LUNCH), "keep")
    rule(app, domain(world, LUNCH), "block")
    row = stored(world, LUNCH)
    assert (row["bucket"], row["is_junk"]) == ("personal/protected", 0)


def test_a_token_changes_only_what_a_rule_decides(world):
    import ingest
    assert ingest.reclassify("%") == 0                           # in a LIKE it covers everything
    assert ingest.reclassify("%.test") == 0
    assert stored(world, LUNCH)["is_junk"] == 0


def test_every_folder_counts(app, world):
    import ingest
    import accounts
    addr = sender(world, RECEIPT)
    assert app.post("/api/archive", {"pk": world.pk(RECEIPT)}).json["ok"]
    ingest.sync_folder(accounts.get(w.ADA), "Archive")
    rule(app, addr, "block")
    assert stored(world, RECEIPT, "Archive")["is_junk"] == 1


def test_the_command_line_does_it_too(world, capsys):
    from mailmind.__main__ import main
    addr = sender(world, LUNCH)
    main(["rules", "block", addr])
    assert stored(world, LUNCH)["is_junk"] == 1
    assert "stored messages classified again" in capsys.readouterr().out
    main(["rules", "remove", addr])
    assert stored(world, LUNCH)["is_junk"] == 0
