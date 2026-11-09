"""The Screener and a sender's cleanup: who a parent domain's rule takes out of them, where the unsubscribe comes from, and the sweep's cap."""
from fixtures import world as w
from mailmind.bootstrap import curation
from mailmind.curation.domain import cleanup_plan
from stored_rows import pk_of
from world_helpers import body_of


def test_hwf_the_screener_skips_senders_a_parent_domain_rule_decided(world):
    from mailmind.bootstrap.curation import rules
    rules.add("example.test", "keep")
    assert [i["from_addr"] for i in curation.screener.queue(w.ADA)["items"]] == []


def test_hwf_cleanup_skips_senders_a_parent_domain_rule_decided(world):
    from mailmind.bootstrap.curation import rules
    for n in range(4):                                       # enough unread issues to rank as noise
        world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-03-newsletter.eml", Message_ID=f"<issue-{n}@loom.example.test>",
                                                Date=f"Sun, 0{n + 1} Nov 2026 07:00:00 +0000"))
    assert "news@loom.example.test" in [s["from_addr"] for s in curation.cleanup.senders()["senders"]]
    rules.add("example.test", "block")                       # every sender in the world is under it
    assert curation.cleanup.senders()["senders"] == []


NEWSLETTER = "ada-inbox-03-newsletter.eml"
LOOM = "news@loom.example.test"


def test_hwf_unsubscribe_comes_from_the_newest_message_with_a_stored_body(app, world):
    world.deliver(w.ADA, "Archive", w.variant(NEWSLETTER, Message_ID="<issue-219@loom.example.test>",
                                              Date="Mon, 02 Nov 2026 07:00:00 +0000",
                                              List_Unsubscribe="<mailto:stop-219@loom.example.test>"), (w.SEEN,))
    from mailmind.bootstrap.mailbox import store
    c = store.conn()        # mail from before ingest kept these headers: its body is all that can say
    c.execute("UPDATE messages SET list_unsub_raw=NULL, list_unsub_method=NULL WHERE lower(from_addr)=?", (LOOM,))
    c.commit()
    c.close()
    app.get(body_of(world.copy(NEWSLETTER)))               # only the older issue's body is stored
    unsub = app.get(f"/api/cleanup/sender?addr={LOOM}").json["unsub"]
    assert unsub["method"] != "none" and unsub.get("mailto_to") != "stop-219@loom.example.test"
    app.get(body_of(w.copy_of(pk_of("<issue-219@loom.example.test>"))))
    assert app.get(f"/api/cleanup/sender?addr={LOOM}").json["unsub"] == {
        "method": "mailto", "mailto_to": "stop-219@loom.example.test"}


def test_hwf_the_cleanup_preview_says_when_the_sweep_is_capped(app, world, monkeypatch):
    for n, day in ((216, "14"), (217, "21")):
        world.deliver(w.ADA, "INBOX", w.variant(NEWSLETTER, Message_ID=f"<issue-{n}@loom.example.test>",
                                                Date=f"Wed, {day} Oct 2026 07:00:00 +0000"))
    monkeypatch.setattr(cleanup_plan, "SWEEP_CAP", 3)
    r = app.get(f"/api/cleanup/sender?addr={LOOM}").json
    assert (r["sweep_count"], r["sweep_capped"]) == (3, False)
    monkeypatch.setattr(cleanup_plan, "SWEEP_CAP", 2)
    r = app.get(f"/api/cleanup/sender?addr={LOOM}").json
    assert (r["sweep_count"], r["sweep_capped"]) == (2, True)
