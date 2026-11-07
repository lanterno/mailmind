"""Curation's routes on their router: what the schemas refuse, what they let by, and the answers each route keeps."""
import json

import pytest

from fixtures import world as w
from mailmind.bootstrap import curation
from mailmind.bootstrap.curation import rules
from mailmind.platform import settings

BAD_REQUEST = {"ok": False, "error": "bad request"}
LOOM = "news@loom.example.test"
NEWSLETTER = "ada-inbox-03-newsletter.eml"


@pytest.mark.parametrize("path, body", [
    ("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": "yes"}),
    ("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": 1}),
    ("/api/cleanup/execute", {"from_addr": LOOM, "block_scope": 5}),
    ("/api/cleanup/execute", {"from_addr": LOOM, "do_unsubscribe": True, "confirm_token": 17}),
    ("/api/cleanup/execute", {"from_addr": LOOM, "undo_group": 5}),
    ("/api/rules", {"token": LOOM, "verdict": 5}),
    ("/api/rules", {"token": LOOM, "verdict": "block", "pk": "x"}),
    ("/api/rules", {"token": LOOM, "verdict": "block", "pk": [1]}),
])
def test_a_value_its_model_refuses_is_the_400_and_nothing_is_decided(app, world, path, body):
    before, where = rules.load(), world.where(NEWSLETTER)
    r = app.post(path, body)
    assert (r.status, r.json) == (400, BAD_REQUEST)
    assert (rules.load(), world.where(NEWSLETTER)) == (before, where)


@pytest.mark.parametrize("dry_run", ["no", 0, 1])
def test_a_dry_run_that_isnt_a_boolean_sweeps_nothing(app, world, dry_run):
    """0 read as false once, and so asked for the real thing."""
    mark = world.mark()
    r = app.post("/api/sweep", {"account": w.ADA, "dry_run": dry_run})
    assert (r.status, r.json) == (400, BAD_REQUEST)
    assert world.writes(mark) == []


def test_a_field_nothing_here_reads_is_nothing_to_refuse(app):
    r = app.post("/api/rules", {"token": LOOM, "verdict": "keep", "subject": 5})
    assert (r.status, r.json["ok"], r.json["placed_in"]) == (200, True, "keep_senders")


def test_a_cleanup_hands_its_use_case_what_was_sent_and_only_that(app, monkeypatch):
    """A confirm token goes through as the preview gave it, spaces and all, and what wasn't sent isn't made up."""
    got = []

    def execute(body):
        got.append(dict(body))
        return {"ok": False, "error": "stood in"}, 500
    monkeypatch.setattr(curation.cleanup, "execute", execute)
    sent = {"from_addr": LOOM, "block_scope": "domain", "do_unsubscribe": True, "confirm_token": " 1.Ab_c-= ", "undo_group": "g"}
    app.post("/api/cleanup/execute", sent)
    app.post("/api/cleanup/execute", {"from_addr": LOOM, "elsewhere": "x"})
    assert got == [sent, {"from_addr": LOOM}]


@pytest.mark.parametrize("given, account", [({}, "all"), ({"account": None}, None), ({"account": w.ADA}, w.ADA)])
@pytest.mark.parametrize("flag, dry", [({}, False), ({"dry_run": None}, False), ({"dry_run": False}, False), ({"dry_run": True}, True)])
def test_a_sweep_left_to_say_nothing(app, monkeypatch, given, account, flag, dry):
    """Naming no account is every account, and saying nothing of a dry run asks for the real thing, as it did."""
    asked = []

    def run(acct, dry_run):
        asked.append((acct, dry_run))
        return {"ok": True, "dry_run": dry_run, "results": [], "generated": "now"}
    monkeypatch.setattr(curation.sweeps, "run", run)
    assert app.post("/api/sweep", {**given, **flag}).status == 200
    assert asked == [(account, dry)]


def test_a_sweep_that_answers_an_account_by_account_failure_fits(app, world):
    """A login refused: the account's result says how far it got, as its model says."""
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] no", times=None)
    r = app.post("/api/sweep", {"account": w.ADA, "dry_run": True})
    assert r.status == 200
    assert (r.json["ok"], r.json["results"][0]["planned"]) == (True, 0)
    assert r.json["results"][0]["error"]


@pytest.mark.parametrize("body, said", [({"token": "nothing", "verdict": "keep"}, "invalid token (need a domain or an address)"),
                                        ({"verdict": "keep"}, "invalid token (need a domain or an address)"),
                                        ({"token": LOOM}, "verdict must be keep|block|remove"),
                                        ({"token": LOOM, "verdict": "maybe"}, "verdict must be keep|block|remove")])
def test_a_rule_that_wont_do_is_still_a_200(app, body, said):
    r = app.post("/api/rules", body)
    assert (r.status, r.json) == (200, {"ok": False, "msg": said})


def test_a_rule_removed_places_nothing(app):
    assert app.post("/api/rules", {"token": LOOM, "verdict": "keep"}).json["placed_in"] == "keep_senders"
    r = app.post("/api/rules", {"token": LOOM, "verdict": "remove"})
    assert (r.status, r.json["placed_in"], r.json["rules"]["keep_senders"]) == (200, None, [])


@pytest.mark.parametrize("query, said", [("", "bad address"), ("?addr=", "bad address"), ("?addr=loom", "bad address"),
                                         ("?addr=nobody@example.test", "no such sender")])
def test_a_sender_that_wont_do_is_still_a_200(app, query, said):
    r = app.get("/api/cleanup/sender" + query)
    assert (r.status, r.json) == (200, {"ok": False, "error": said})


def test_a_cleanup_refused_says_so_in_its_own_words(app):
    r = app.post("/api/cleanup/execute", {"from_addr": "loom"})
    assert (r.status, r.json) == (400, {"ok": False, "error": "bad address"})


def test_a_cleanup_that_breaks_keeps_its_own_words(app, monkeypatch):
    def broken(body):
        raise RuntimeError("what a server said")
    monkeypatch.setattr(curation.cleanup, "execute", broken)
    r = app.post("/api/cleanup/execute", {"from_addr": LOOM})
    assert (r.status, r.json) == (500, {"ok": False, "error": "cleanup failed"})


def test_a_query_string_reads_as_it_did(app, world):
    first, second = LOOM, "nobody@example.test"
    assert app.get(f"/api/cleanup/sender?addr={first}&addr={second}").json["from_addr"] == first
    assert app.get(f"/api/cleanup/sender?addr=&addr={first}").json["from_addr"] == first
    assert app.get("/api/audit?limit=").json == {"events": [], "has_more": False}
    assert app.get("/api/screener?account=").json == {"ok": True, "account": None, "items": [], "total": 0}
    assert app.get("/api/screener?account=nobody@example.test").json["account"] == "nobody@example.test"
    assert app.get(f"/api/screener?account={w.ADA}&account=nobody@example.test").json["account"] == w.ADA


def test_each_kind_of_audit_line_fits_its_model(app):
    """Written here as each writer writes it, since what the suite reaches of a real sweep or reconcile may leave one out."""
    now = "2026-11-02T12:00:00+00:00"
    sweep_lines = [{"kind": "sweep", "ts": now, "account": w.ADA, "uid": "7", "from": LOOM, "subject": "Issue", "bucket": "MKT", "reason": "x"},
                   {"kind": "sender_cleanup", "ts": now, "from": LOOM, "token": LOOM, "swept": 3, "status": "skipped"}]
    reconcile_lines = [{"kind": "reconcile", "ts": now, "auto": True, "account": w.ADA, "pruned": 1, "live": 9},
                       {"kind": "reconcile", "ts": now, "account": w.ADA, "pruned": 2, "live": 8}]
    for path, lines in ((settings.current().sweep_audit, sweep_lines), (settings.current().reconcile_audit, reconcile_lines)):
        with open(path, "w") as f:
            f.writelines(json.dumps(line) + "\n" for line in lines)
    r = app.get("/api/audit")
    assert (r.status, len(r.json["events"]), r.json["has_more"]) == (200, 4, False)
    assert r.json["events"][0]["from"] == LOOM
