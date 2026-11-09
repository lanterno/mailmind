"""Drafting a reply, and drafting one ahead: when a banked draft may be served, what may spend one, the tick and its backoff,
and what is kept of the cache. The rounds run in the test's own thread, the frozen clock moved on before each (see run_tick)."""
import time
from datetime import timedelta
from urllib.parse import urlencode

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.bootstrap import assistant
from mailmind.bootstrap.assistant import answers
from assistant_world import CLIENT, LUNCH, LUNCH_MID, QUILT, execute, pk_of, renumber_inbox, summarize_as
from stored_rows import A, NOW_TS, _ahead, _ahead_keys, put, q, summary
from world_helpers import VENUE_END, body_of

ATELIER = "ada-inbox-09-rfc2047-folded.eml"     # Renee's workshop plan: another thread of one, inside 72h
DRAFTING = "You are drafting a reply"


def put_draft(account, ref_key, created, hit=None, c=None):
    """A banked draft row, created at `created` and last opened at `hit`."""
    from mailmind.bootstrap.mailbox import store
    own = c is None
    c = store.conn() if own else c
    try:
        answers.draft_ahead_put(c, account, ref_key, "fp", "An old draft.", "balanced", "claude-opus-5-5",
                              "<gone@example.test>", "dwell", created)
        if hit is not None:
            answers.draft_ahead_touch(c, account, ref_key, hit)
        c.commit()
    finally:
        if own:
            c.close()


def draft_rows(account, ref_key):
    return q("SELECT body, fp, fail_count FROM draft_ahead WHERE account=? AND ref_key=?", account, ref_key)


def thread_key(world, name):
    return "t:" + world.row(name)["thread_id"]


def drafts(ai):
    return [r for r in ai.requests if r.prompt.startswith(DRAFTING)]


def ahead(pk, **more):
    """Draft-ahead's route for the copy the row `pk` is now, by its key and folder."""
    return "/api/ai/draft-ahead?" + urlencode({**w.copy_of(pk), **more})


def peek(app, pk):
    return app.get(ahead(pk, peek=1)).json


def kick(app, pk):
    return app.get(ahead(pk)).json


def run_tick(app, monkeypatch, cycles, step=None):
    """Run `cycles` rounds of a fresh pre-drafter in this thread, as the background job would: the
    frozen clock moves on before each, by the startup delay and then the interval, or by `step`
    seconds each time."""
    draft_ahead_round = assistant.drafting.rounds()
    for i in range(cycles):
        wait = assistant.drafting.DRAFT_TICK_STARTUP_DELAY if i == 0 else assistant.drafting.DRAFT_TICK_INTERVAL
        app.clock.shift(timedelta(seconds=wait if step is None else step))
        draft_ahead_round()


@pytest.fixture
def present(app, monkeypatch):
    """Someone is at the screen now, as far as the governor's presence gate can tell."""
    from mailmind.bootstrap import assistant
    assistant.governor().saw_someone()


STALE = "Stale: see you at 12:30."


def moves_on_mid_draft(world):
    """A responder under which Lin writes again while the lunch reply is being drafted."""
    late = w.variant(LUNCH, Message_ID="<lunch-9@lin.example.test>", Date="Mon, 02 Nov 2026 11:50:00 +0000")

    def reply(req):
        if req.prompt.startswith(DRAFTING):
            world.deliver(w.ADA, "INBOX", late)
            return fa.answer({"body": STALE})
        return None
    return reply


def test_hwf_a_thread_that_moves_on_during_a_pre_draft_is_drafted_again(app, world, ai):
    anchor = world.pk(LUNCH)
    summarize_as(anchor, 5, "action-needed", "Reply")
    ai.responder = moves_on_mid_draft(world)
    assert kick(app, anchor)["status"] == "generating"
    app.settle()
    ai.responder = None
    assert [r["body"] for r in draft_rows(w.ADA, thread_key(world, LUNCH))] == [STALE]
    newest = pk_of("<lunch-9@lin.example.test>")
    assert peek(app, newest)["status"] == "none"
    assert app.post("/api/ai/draft-reply", w.copy_of(newest)).json["body"] != STALE


def test_hwf_a_thread_that_moves_on_during_a_draft_click_is_not_served_from_the_bank(app, world, ai):
    ai.responder = moves_on_mid_draft(world)
    assert app.post("/api/ai/draft-reply", world.copy(LUNCH)).json["body"] == STALE
    ai.responder = None
    assert app.post("/api/ai/draft-reply", w.copy_of(pk_of("<lunch-9@lin.example.test>"))).json["body"] != STALE


def test_hwf_a_pre_draft_survives_its_folder_being_renumbered(app, world, ai):
    anchor = world.pk(LUNCH)
    ai.queue(fa.answer({"body": "See you at 12:30."}))
    assert app.post("/api/ai/draft-reply", world.copy(LUNCH)).status == 200      # banked for the thread
    renumber_inbox(world, w.ADA)
    moved = pk_of(LUNCH_MID)
    assert moved != anchor and w.copy_of(moved) == world.copy(LUNCH)          # a new pk, the same key and folder
    assert peek(app, moved) == {"ok": True, "status": "ready", "body": "See you at 12:30.", "tone": "balanced"}


def test_hwf_one_thread_pays_for_one_default_draft_served_only_at_its_newest(app, world, ai):
    older, anchor = world.pk("ada-sent-01-lunch-reply.eml"), world.pk(LUNCH)
    a = app.post("/api/ai/draft-reply", w.copy_of(older)).json
    b = app.post("/api/ai/draft-reply", w.copy_of(anchor)).json
    assert len(drafts(ai)) == 1 and a["body"] == b["body"]
    assert peek(app, older)["status"] == "none"          # never paired with a composer quoting an older message
    assert peek(app, anchor)["status"] == "ready"


def test_hwf_a_pre_draft_rechecks_the_cache_after_winning_the_lead(app, world, ai):
    anchor = world.pk(LUNCH)
    assert app.post("/api/ai/draft-reply", world.copy(LUNCH)).status == 200
    ai.requests.clear()
    assert assistant.drafting._gen_draft_ahead(w.ADA, anchor) is None
    assert ai.requests == []


# (importance, category, action) -> whether opening the message may spend a pre-draft
KICK_BAR = (
    ((5, "info", ""), True),
    ((4, "personal", ""), True),
    ((2, "action-needed", ""), True),
    ((3, "personal", "Reply with a date"), False),     # a next step alone spends nothing
    ((3, "work-job", ""), False),
)


def test_hwf_only_important_or_action_mail_kicks_a_pre_draft(app, world, ai):
    pk = world.pk(QUILT)
    got = []
    for (importance, category, action), _ in KICK_BAR:
        summarize_as(pk, importance, category, action)
        execute("DELETE FROM draft_ahead")
        got.append(kick(app, pk)["status"] == "generating")
        app.settle()
    assert got == [want for _, want in KICK_BAR]


def test_hwf_the_tick_drafts_by_the_same_bar(app, world, ai, monkeypatch, present):
    pk = world.pk(QUILT)
    got = []
    for (importance, category, action), _ in KICK_BAR:
        summarize_as(pk, importance, category, action)
        execute("DELETE FROM draft_ahead")
        ai.requests.clear()
        run_tick(app, monkeypatch, cycles=1)
        got.append(len(drafts(ai)) == 1)
    assert got == [want for _, want in KICK_BAR]


def test_hwf_the_client_kick_bar_is_what_the_server_says(app, world):
    # The app has no copy of the rule. It reads `reply_worthy` off the row the server listed, and
    # that is the bar the kick route holds to, tested above.
    pk = world.pk(QUILT)
    said = []
    for (importance, category, action), _ in KICK_BAR:
        summarize_as(pk, importance, category, action)
        [row] = [i for i in app.get(f"/api/messages?account={w.ADA}&folder=INBOX&limit=100").json["items"]
                 if i["key"] == world.key(QUILT)]
        said.append(row["reply_worthy"])
    assert said == [want for _, want in KICK_BAR]


def test_hwf_a_warm_pre_draft_answers_the_draft_button_for_free(app, world, ai):
    pk = world.pk(QUILT)
    summarize_as(pk, 5, "personal")
    ai.queue(fa.answer({"body": "It looks wonderful, well done!"}))
    assert kick(app, pk)["status"] == "generating"
    app.settle()
    ai.requests.clear()
    r = app.post("/api/ai/draft-reply", world.copy(QUILT)).json
    assert (r["body"], r["model"]) == ("It looks wonderful, well done!", "claude-opus-5-5")
    assert ai.requests == []


def test_hwf_a_custom_draft_is_never_banked(app, world, ai):
    pk = world.pk(QUILT)
    for custom in ({"tone": "warm"}, {"instruction": "Say I can't make it"}):
        assert app.post("/api/ai/draft-reply", {**world.copy(QUILT), **custom}).status == 200
    assert len(drafts(ai)) == 2
    assert peek(app, pk)["status"] == "none"
    assert draft_rows(w.ADA, thread_key(world, QUILT)) == []


def test_hwf_a_failed_bank_write_still_returns_the_draft(app, world, ai, monkeypatch):
    import sqlite3

    def full_disk(*a, **kw):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(answers, "draft_ahead_put", full_disk)
    r = app.post("/api/ai/draft-reply", world.copy(QUILT))
    assert r.status == 200 and r.json["body"]


@pytest.mark.parametrize("outcomes", [
    [False, None, None, False, None, False, True, True],     # a neutral None neither counts nor resets
    [False, False, True, False, False, False, True, True],   # a success resets the count
])
def test_hwf_the_tick_breaker_opens_after_three_failures_it_led(app, monkeypatch, present, outcomes):
    ran = []

    def gen(account, pk, source="dwell"):
        ran.append(pk)
        return outcomes[len(ran) - 1]
    monkeypatch.setattr(assistant.drafting, "_pick_draft_candidate", lambda: {"account": w.ADA, "pk": 1})
    monkeypatch.setattr(assistant.drafting, "_gen_draft_ahead", gen)
    run_tick(app, monkeypatch, cycles=len(outcomes))
    assert len(ran) == 6                 # the third failure opens the breaker for the next 30 minutes


def test_hwf_a_restart_during_an_outage_keeps_the_persisted_backoff(app, world, ai, monkeypatch):
    import app_client
    pk = world.pk(QUILT)
    summarize_as(pk, 5, "personal")
    asked = []

    def outage(req):
        if req.prompt.startswith(DRAFTING):
            asked.append(int(time.time()))
            return fa.error(400, "invalid_request_error", "bad request")
        return None
    ai.responder = outage
    kick(app, pk)
    app.settle()
    app_client.reset_state()                            # the process restarts; only the table remembers
    run_tick(app, monkeypatch, cycles=6)                # cycles at +120s, then every 180s
    assert [t - asked[0] for t in asked] == [0, 300, 1020]          # 300s, then 600s, after each failure
    assert draft_rows(w.ADA, thread_key(world, QUILT))[0]["fail_count"] == 3


def test_hwf_the_tick_skips_a_warm_draft_and_drafts_the_next(app, world, ai, monkeypatch):
    import app_client
    warm, cold = world.pk(QUILT), world.pk(ATELIER)
    summarize_as(warm, 5, "personal")
    summarize_as(cold, 4, "work-job")
    kick(app, warm)
    app.settle()
    ai.requests.clear()
    app_client.reset_state()                            # a restart, which counts as someone here
    run_tick(app, monkeypatch, cycles=1)
    [req] = drafts(ai)
    assert "renee@atelier.example.test" in req.prompt and "lin@example.test" not in req.prompt


def test_hwf_the_tick_stops_when_only_background_requests_come_in(app, world, ai, monkeypatch, present):
    summarize_as(world.pk(QUILT), 5, "personal")
    app.clock.shift(timedelta(minutes=46))
    for path in (ahead(world.pk(QUILT), peek=1), f"/api/folders?account={w.ADA}",
                 "/api/stats", "/api/outbox"):                # what a hidden tab re-fetches on its own
        app.get(path)
    run_tick(app, monkeypatch, cycles=1)
    assert drafts(ai) == []
    app.get("/api/digest")                                     # someone opens the app
    run_tick(app, monkeypatch, cycles=1)
    assert len(drafts(ai)) == 1


def test_hwf_the_tick_yields_for_45s_after_interactive_ai(app, world, ai, monkeypatch, present):
    summarize_as(world.pk(QUILT), 5, "personal")
    app.post("/api/ai/rewrite", {"op": "fix", "text": "teh"})
    run_tick(app, monkeypatch, cycles=4, step=10)              # 10s to 40s after the rewrite
    assert drafts(ai) == []
    run_tick(app, monkeypatch, cycles=1, step=10)              # 50s after it
    assert len(drafts(ai)) == 1


def test_hwf_the_tick_waits_while_an_interactive_call_runs(app, world, ai, monkeypatch, present):
    from mailmind.bootstrap import assistant
    summarize_as(world.pk(QUILT), 5, "personal")
    with assistant.governor().call("interactive", "rewrite", assistant.calls.MODEL, None):   # one in flight
        run_tick(app, monkeypatch, cycles=2)
        assert drafts(ai) == []
    run_tick(app, monkeypatch, cycles=1)                       # 120s after it ended
    assert len(drafts(ai)) == 1


GONE = "t:rf:ada@example.test:0000000000000000"     # a thread with no messages here (mid-resync, say)


def test_hwf_drafting_never_sweeps_orphans(app, world, ai):
    put_draft(w.ADA, GONE, created=int(time.time()) - 7200)
    pk = world.pk(QUILT)
    summarize_as(pk, 5, "personal")
    kick(app, pk)
    app.settle()
    assert draft_rows(w.ADA, thread_key(world, QUILT))[0]["body"]      # the draft landed, and its GC ran
    assert len(draft_rows(w.ADA, GONE)) == 1


def test_hwf_the_tick_sweeps_orphans_after_an_hour(app, world, ai, monkeypatch, present):
    young = "t:rf:ada@example.test:1111111111111111"
    put_draft(w.ADA, GONE, created=int(time.time()) - 7200)
    put_draft(w.ADA, young, created=int(time.time()) - 600)
    run_tick(app, monkeypatch, cycles=1)
    assert draft_rows(w.ADA, GONE) == [] and len(draft_rows(w.ADA, young)) == 1


def test_hwf_a_draft_whose_thread_ends_in_your_own_sent_copy_is_dropped(app, world, ai, monkeypatch, present):
    assert app.post("/api/ai/draft-reply", world.copy(LUNCH)).status == 200      # banked
    key = thread_key(world, LUNCH)
    world.deliver(w.ADA, "Sent", w.variant("ada-sent-01-lunch-reply.eml", Message_ID="<lunch-4@ada.example.test>",
                                           Date="Mon, 02 Nov 2026 11:00:00 +0000"))
    [newest] = q("SELECT folder, answered FROM messages WHERE account=? AND thread_id=? ORDER BY ts DESC LIMIT 1",
                 w.ADA, key[2:])
    assert newest == {"folder": "Sent", "answered": 0}
    run_tick(app, monkeypatch, cycles=1)
    assert draft_rows(w.ADA, key) == []


def test_hwf_draft_gc_evicts_by_last_use_counting_a_regeneration_as_use(data_dir):
    from mailmind.bootstrap.mailbox import store
    now = int(time.time())
    c = store.conn()
    try:
        for i in range(498):
            put_draft(w.ADA, f"t:filler-{i}", created=now - 100, c=c)
        put_draft(w.ADA, "t:regenerated", created=now - 60, hit=now - 3000, c=c)     # rewritten a minute ago
        put_draft(w.ADA, "t:opened", created=now - 2600, hit=now - 5, c=c)           # old, but opened just now
        put_draft(w.ADA, "t:idle", created=now - 2500, c=c)                          # the least recently useful
        answers.draft_ahead_gc(c, now, orphans=False)
        c.commit()
        left = {r["ref_key"] for r in c.execute("SELECT ref_key FROM draft_ahead")}
    finally:
        c.close()
    assert len(left) == 500 and "t:idle" not in left and {"t:regenerated", "t:opened"} <= left


def test_a_draft_survives_switching_the_from_account(app, world, ai):
    assert app.post("/api/ai/draft-reply", {**world.copy(CLIENT), "account": w.ADA}).status == 200
    [asked] = ai.requests
    assert w.ADA in asked.prompt and "Contract draft v2" in asked.prompt     # Ada answers Bea's mail


def test_a_thread_that_ends_in_your_own_reply_is_not_pre_drafted(app, world, ai):
    world.deliver(w.ADA, "Sent", w.variant("ada-sent-01-lunch-reply.eml", Message_ID="<lunch-4@ada.example.test>",
                                           Date="Mon, 02 Nov 2026 11:00:00 +0000"))
    mine = pk_of("<lunch-4@ada.example.test>", folder="Sent")
    summarize_as(mine, 5, "action-needed", "Follow up with Lin")
    for _ in range(2):                            # opening your own reply twice
        kick(app, mine)
        app.settle()
    assert draft_rows(w.ADA, thread_key(world, LUNCH)) == []        # nothing kept, as the GC intends
    assert len(drafts(ai)) == 0


DAY = 86400


def test_hwf_draft_ahead_ages_out_after_a_week_unless_a_failure_is_recent(db):
    for t in "ABCD":
        put(db, f"<{t}@example.test>", thread_id=t)
    _ahead(db, "t:A", NOW_TS - 8 * DAY)
    _ahead(db, "t:B", NOW_TS - 8 * DAY)
    answers.draft_ahead_fail(db, A, "t:B", NOW_TS - DAY)       # a failed regeneration keeps the row as backoff
    answers.draft_ahead_fail(db, A, "t:C", NOW_TS - DAY)       # failure-only rows persist the backoff
    answers.draft_ahead_fail(db, A, "t:D", NOW_TS - 8 * DAY)
    answers.draft_ahead_gc(db, NOW_TS)
    assert _ahead_keys(db) == ["t:B", "t:C"]


def test_hwf_draft_ahead_sweeps_orphans_only_when_asked_and_after_an_hour(db):
    _ahead(db, "t:gone", NOW_TS - 2 * 3600)
    _ahead(db, "m:<gone@example.test>", NOW_TS - 2 * 3600)
    _ahead(db, "t:just-made", NOW_TS - 600)
    answers.draft_ahead_gc(db, NOW_TS, orphans=False)          # the opportunistic call sites: mid-sync safe
    assert _ahead_keys(db) == ["m:<gone@example.test>", "t:gone", "t:just-made"]
    answers.draft_ahead_gc(db, NOW_TS)
    assert _ahead_keys(db) == ["t:just-made"]


def test_hwf_draft_ahead_drops_a_draft_once_the_conversation_ended(db):
    put(db, "<s1@example.test>", thread_id="SENT", ts=100)
    put(db, "<s2@example.test>", thread_id="SENT", ts=200, folder="Sent")      # a synced Sent copy: answered=0
    put(db, "<a1@example.test>", thread_id="ANSWERED", ts=100, answered=1)
    put(db, "<o1@example.test>", thread_id="OPEN", ts=100, folder="Sent")
    put(db, "<o2@example.test>", thread_id="OPEN", ts=200)                     # they wrote back last
    put(db, "<f1@example.test>", thread_id="FAILED", ts=200, folder="Sent")
    put(db, "<j@example.test>", is_junk=1)
    put(db, "<k@example.test>", answered=1)
    put(db, "<o@example.test>")
    for ref in ("t:SENT", "t:ANSWERED", "t:OPEN", "m:<j@example.test>", "m:<k@example.test>", "m:<o@example.test>"):
        _ahead(db, ref, NOW_TS - 60)
    answers.draft_ahead_fail(db, A, "t:FAILED", NOW_TS - 60)   # no body: only a backoff record, never moot
    answers.draft_ahead_gc(db, NOW_TS, orphans=False)
    assert _ahead_keys(db) == ["m:<o@example.test>", "t:FAILED", "t:OPEN"]


def test_hwf_draft_ahead_keeps_the_500_most_recently_useful(db):
    for i in range(502):
        _ahead(db, f"t:x{i:03}", NOW_TS - 1000 + i)
    answers.draft_ahead_touch(db, A, "t:x000", NOW_TS)          # oldest made, but just served
    answers.draft_ahead_gc(db, NOW_TS, orphans=False)
    keys = _ahead_keys(db)
    assert len(keys) == 500 and "t:x000" in keys and not {"t:x001", "t:x002"} & set(keys)


VENUE_START = "cy-inbox-01-thread-start.eml"


def triaged_venue(world):
    """Mae's last word in Cy's venue thread, called worth a reply; returns its copy."""
    summary(world.pk(VENUE_END), 5, "action-needed", "Confirm the date")
    return world.copy(VENUE_END)


def draft_ahead(app, copy, peek=False):
    return app.get("/api/ai/draft-ahead?" + urlencode({**copy, **({"peek": 1} if peek else {})})).json["status"]


@pytest.mark.parametrize("reply_warm", [False, True], ids=["drafting-fetches-a-body", "only-the-open-fetches"])
def test_hwf_opening_a_message_keeps_its_threads_draft_ready(app, world, ai, reply_warm):
    # Cy's sent reply has no snippet, so writing the draft fetches its body. Warmed first, the
    # draft fetches nothing and opening the thread's first message is the only change after it.
    end = triaged_venue(world)
    if reply_warm:
        assert app.get(body_of(world.copy("cy-sent-01-thread-reply.eml"))).json["ok"]
    assert world.row("cy-sent-01-thread-reply.eml")["body_fetched"] == int(reply_warm)
    assert draft_ahead(app, end) == "generating"
    app.settle()
    assert draft_ahead(app, end) == "ready"
    assert world.row(VENUE_START)["body_fetched"] == 0
    assert app.get(body_of(world.copy(VENUE_START))).json["ok"]
    assert world.row(VENUE_START)["body_fetched"] == 1
    assert draft_ahead(app, end) == "ready"
