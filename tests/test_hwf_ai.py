"""The AI features' hard-won fixes, pinned before the refactor moves them: what a call that fails
or comes back unusable leaves behind, how the brief, delta and review caches are keyed, when a
pre-drafted reply may be served, what the draft-ahead tick spends and when it holds back, and the
guards around do-plan, the review and banking. Each test names one behaviour a rewrite could lose
without any other test noticing.

Everything runs against the fake Claude API (tests/fakes/anthropic_api.py) and the fictional
world. The draft-ahead rounds run in the test's own thread, the frozen clock moved on before each
as the background wait between them would (see run_tick).
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w

LUNCH = "ada-inbox-02-lunch-confirm.eml"        # Lin's last word: the anchor of Ada's lunch thread
LUNCH_MID = "<lunch-3@lin.example.test>"
QUILT = "ada-inbox-11-html-cid.eml"             # Lin's quilt photo: a thread of one, inside the tick's 72h
ATELIER = "ada-inbox-09-rfc2047-folded.eml"     # Renee's workshop plan: another thread of one, inside 72h
CLIENT = "bea-inbox-01-client.eml"              # Ivo's contract mail, in Bea's account
VENUE_END = "cy-inbox-02-thread-end.eml"        # Mae's last word in Cy's Gmail thread
DRAFTING = "You are drafting a reply"


# -- helpers --------------------------------------------------------------------------------------

def q(sql, *args):
    import store
    c = store.conn()
    try:
        return [dict(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def execute(sql, *args):
    import store
    c = store.conn()
    try:
        c.execute(sql, args)
        c.commit()
    finally:
        c.close()


def summarize_as(pk, importance, category="info", action=""):
    """Store the triage result a model would have given this message."""
    import store
    c = store.conn()
    try:
        store.save_summary(c, pk, "Stub triage.", importance, category, action, "claude-opus-5-5",
                           datetime.now(timezone.utc).isoformat())
        c.commit()
    finally:
        c.close()


def put_draft(account, ref_key, created, hit=None, c=None):
    """A banked draft row, created at `created` and last opened at `hit`."""
    import store
    own = c is None
    c = store.conn() if own else c
    try:
        store.draft_ahead_put(c, account, ref_key, "fp", "An old draft.", "balanced", "claude-opus-5-5", 1,
                              "<gone@example.test>", "dwell", created)
        if hit is not None:
            store.draft_ahead_touch(c, account, ref_key, hit)
        c.commit()
    finally:
        if own:
            c.close()


def draft_rows(account, ref_key):
    return q("SELECT body, fp, fail_count FROM draft_ahead WHERE account=? AND ref_key=?", account, ref_key)


def thread_key(world, name):
    return "t:" + world.row(name)["thread_id"]


def pk_of(message_id, folder="INBOX"):
    [r] = q("SELECT pk FROM messages WHERE message_id=? AND folder=?", message_id, folder)
    return r["pk"]


def drafts(ai):
    return [r for r in ai.requests if r.prompt.startswith(DRAFTING)]


def peek(app, pk):
    return app.get(f"/api/ai/draft-ahead?pk={pk}&peek=1").json


def kick(app, pk):
    return app.get(f"/api/ai/draft-ahead?pk={pk}").json


def renumber_inbox(world, account):
    """The server renumbers the folder (a UIDVALIDITY change) and the app re-syncs it: every
    INBOX row comes back under a new pk."""
    import accounts
    import ingest
    world.server.renumber(account, "INBOX")
    ingest.sync_new(accounts.get(account))


def run_tick(app, monkeypatch, cycles, step=None):
    """Run `cycles` rounds of a fresh pre-drafter in this thread, as the background job would: the
    frozen clock moves on before each, by the startup delay and then the interval, or by `step`
    seconds each time."""
    import server
    draft_ahead_round = server.draft_ahead_rounds()
    for i in range(cycles):
        wait = server.DRAFT_TICK_STARTUP_DELAY if i == 0 else server.DRAFT_TICK_INTERVAL
        app.clock.shift(timedelta(seconds=wait if step is None else step))
        draft_ahead_round()


@pytest.fixture
def present(app, monkeypatch):
    """Someone is at the screen now, as far as the governor's presence gate can tell."""
    from mailmind.bootstrap import assistant
    assistant.governor().saw_someone()


# -- the gateway ----------------------------------------------------------------------------------

def test_hwf_triage_retries_a_failed_call_but_stores_an_unusable_reply_once(world, ai):
    import summarize

    def reply(req):
        if "Subject: Contract draft v2" in req.prompt:
            return fa.error(400, "invalid_request_error", "bad request")
        if "Subject: The finished quilt" in req.prompt:
            return fa.answer({})                     # a reply with nothing usable in it
        return None
    ai.responder = reply
    summarize.summarize_pending(limit=100, workers=1)
    assert q("SELECT model FROM summaries WHERE message_pk=?", world.pk(QUILT)) == [{"model": "parse-error"}]
    assert q("SELECT model FROM summaries WHERE message_pk=?", world.pk(CLIENT)) == []
    ai.requests.clear()
    summarize.summarize_pending(limit=100, workers=1)
    assert [r.prompt.count("Subject: Contract draft v2") for r in ai.requests] == [1]


def test_hwf_only_the_text_after_the_last_fallback_block_counts(data_dir, ai):
    import summarize
    hop = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"}}
    ai.queue((200, {"type": "message", "role": "assistant", "stop_reason": "end_turn", "content": [
        {"type": "text", "text": '{"summary": "first try'}, hop,
        {"type": "text", "text": '{"summary": "second try'}, hop,
        {"type": "text", "text": '{"summary": "final", "importance": 3, "category": "info"}'}]}))
    assert summarize.call_claude_json("Triage this.", summarize.TRIAGE_SCHEMA)["summary"] == "final"


def test_hwf_api_schema_reads_defs_as_names_not_keywords():
    from mailmind.assistant.domain.schema import structured
    schema = {"type": "object", "$defs": {"minimum": {"type": "integer", "minimum": 0}},
              "properties": {"n": {"$ref": "#/$defs/minimum"}}}
    assert structured(schema)["$defs"] == {"minimum": {"type": "integer"}}


def test_hwf_the_rewrite_prompt_never_asks_for_json(app, world, ai):
    assert app.post("/api/ai/rewrite", {"op": "shorten", "text": "thanks, see you thursday"}).status == 200
    [req] = ai.requests
    assert "json" not in req.prompt.lower()
    assert req.schema["properties"] == {"text": {"type": "string"}}     # the envelope is the schema's job


def test_hwf_a_do_command_with_braces_reaches_the_prompt_verbatim(app, world, ai):
    cmd = 'archive {cmd} and {today} from {0} {"intent": "act"}'
    assert app.post("/api/ai/do-plan", {"cmd": cmd}).status == 200
    [req] = ai.requests
    assert "COMMAND: <<<" + cmd + ">>>" in req.prompt
    assert '"delete everything" -> {"intent":"search","filter":""}' in req.prompt   # the template's own JSON
    assert req.prompt.count("2026-11-02") == 1


# -- failures and fallbacks of the cached AI reads ------------------------------------------------

AI_READS = {
    "brief": lambda app, world: app.post("/api/briefing", {}),
    "delta": lambda app, world: app.post("/api/briefing/since", {"since": 0}),
    "review": lambda app, world: app.post("/api/review", {}),
    "thread summary": lambda app, world: app.post("/api/ai/summarize", {"pk": world.pk(LUNCH)}),
}
UNUSABLE = {"brief": fa.answer({}), "delta": fa.answer({}), "review": fa.answer({}),
            "thread summary": fa.answer("Sorry, there is no JSON in this reply.")}
FALLBACK = {
    "brief": lambda r: r["model"] == "fallback",
    "delta": lambda r: r["headline"].endswith(" new since you last looked."),
    "review": lambda r: r["model"] == "fallback",
    "thread summary": lambda r: r["model"] == "parse-error",
}


@pytest.mark.parametrize("which", list(AI_READS))
def test_hwf_a_failed_call_is_retried_but_an_unusable_reply_is_cached_once(app, world, ai, which):
    summarize_as(world.pk(LUNCH), 5, "action-needed", "Reply")      # the brief needs a triaged message
    ai.queue(fa.error(400, "invalid_request_error", "bad request"), UNUSABLE[which])
    assert AI_READS[which](app, world).status == 502                # nothing cached: the next load asks again
    retried = AI_READS[which](app, world)
    assert retried.status == 200 and FALLBACK[which](retried.json)
    again = AI_READS[which](app, world)
    assert again.status == 200 and again.json["cached"] is True and len(ai.requests) == 2


# -- the brief's narrative and the daily caches ---------------------------------------------------

def brief_narrative(app, world, ai, narrative):
    summarize_as(world.pk(LUNCH), 5, "action-needed", "Reply")
    ai.queue(fa.answer({"narrative": narrative, "groups": []}))
    return app.post("/api/briefing/refresh", {}).json["narrative"]


def test_hwf_a_long_brief_is_kept_whole_under_the_cap(app, world, ai):
    told = "".join(f"Item {i} needs a look today. " for i in range(40)).strip()     # about 1200 chars
    assert 800 < len(told) < 1500
    assert brief_narrative(app, world, ai, told) == told


def test_hwf_a_runaway_brief_ends_on_its_last_whole_sentence(app, world, ai):
    told = "".join(f"Item {i} needs a look today. " for i in range(90))
    got = brief_narrative(app, world, ai, told)
    assert 750 < len(got) <= 1500
    assert told.startswith(got) and got.endswith(".") and told[len(got)] == " "


def test_hwf_a_runaway_brief_without_sentences_ends_on_a_word_and_an_ellipsis(app, world, ai):
    told = "word " * 400
    got = brief_narrative(app, world, ai, told)
    assert got.endswith("\u2026")
    kept = got[:-1]
    assert 750 < len(kept) <= 1500 and told.startswith(kept) and told[len(kept)] == " "


@pytest.mark.parametrize("route", ["/api/briefing", "/api/review"])
def test_hwf_the_daily_ai_cards_roll_at_local_midnight(app, world, ai, route):
    summarize_as(world.pk(LUNCH), 5, "action-needed", "Reply")
    os.environ["TZ"] = "XXX-10"           # UTC+10: the frozen noon UTC is 22:00 here
    time.tzset()
    try:
        assert app.post(route, {}).status == 200
        app.clock.shift(timedelta(hours=1))           # 23:00 local
        app.post(route, {})
        assert len(ai.requests) == 1
        app.clock.shift(timedelta(hours=1))           # local midnight, though UTC is still on the same day
        app.post(route, {})
        assert len(ai.requests) == 2
    finally:
        os.environ["TZ"] = "UTC"
        time.tzset()


# -- the "since you last looked" delta --------------------------------------------------------------

def test_hwf_a_stale_watermark_keeps_one_delta_for_ten_minutes(app, world, ai):
    assert app.post("/api/briefing/since", {"since": 0}).json["cached"] is False
    app.clock.shift(timedelta(minutes=5))            # the 24h floor moved, but not to a new 10-minute step
    assert app.post("/api/briefing/since", {"since": 0}).json["cached"] is True
    assert len(ai.requests) == 1


def test_hwf_a_watermark_ahead_of_the_server_is_held_to_now(app, world, ai):
    world.deliver(w.ADA, "INBOX", w.variant(LUNCH, Message_ID="<lunch-future@lin.example.test>",
                                            Date="Mon, 02 Nov 2026 14:00:00 +0000"))    # dated two hours ahead
    now = int(time.time())
    first = app.post("/api/briefing/since", {"since": now + 3600}).json
    later = app.post("/api/briefing/since", {"since": now + 5400}).json
    assert (first["count"], first["cached"], later["cached"]) == (1, False, True)
    assert len(ai.requests) == 1


def test_hwf_a_delta_is_rewritten_when_its_window_grows(app, world, ai):
    since = int(time.time()) - 6 * 3600
    n = app.post("/api/briefing/since", {"since": since}).json["count"]
    assert app.post("/api/briefing/since", {"since": since}).json["cached"] is True
    world.deliver(w.ADA, "INBOX", w.variant(LUNCH, Message_ID="<lunch-late@lin.example.test>",
                                            Date="Mon, 02 Nov 2026 11:50:00 +0000"))
    grown = app.post("/api/briefing/since", {"since": since}).json
    assert (grown["count"], grown["cached"]) == (n + 1, False)
    assert len(ai.requests) == 2


def test_hwf_the_delta_counts_newsletters_but_not_scams(app, world, ai):
    world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-03-newsletter.eml", Message_ID="<issue-219@loom.example.test>",
                                            Date="Mon, 02 Nov 2026 09:00:00 +0000"))
    app.post("/api/briefing/since", {"since": 0})
    [req] = ai.requests
    assert "The Weekly Loom" in req.prompt
    assert "CONGRATULATIONS" not in req.prompt


# -- when a pre-drafted reply may be served -------------------------------------------------------

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
    assert app.post("/api/ai/draft-reply", {"pk": newest}).json["body"] != STALE


def test_hwf_a_thread_that_moves_on_during_a_draft_click_is_not_served_from_the_bank(app, world, ai):
    ai.responder = moves_on_mid_draft(world)
    assert app.post("/api/ai/draft-reply", {"pk": world.pk(LUNCH)}).json["body"] == STALE
    ai.responder = None
    assert app.post("/api/ai/draft-reply", {"pk": pk_of("<lunch-9@lin.example.test>")}).json["body"] != STALE


def test_hwf_a_pre_draft_survives_its_folder_being_renumbered(app, world, ai):
    anchor = world.pk(LUNCH)
    ai.queue(fa.answer({"body": "See you at 12:30."}))
    assert app.post("/api/ai/draft-reply", {"pk": anchor}).status == 200          # banked for the thread
    renumber_inbox(world, w.ADA)
    moved = pk_of(LUNCH_MID)
    assert moved != anchor
    assert peek(app, moved) == {"ok": True, "status": "ready", "body": "See you at 12:30.", "tone": "balanced"}


def test_hwf_one_thread_pays_for_one_default_draft_served_only_at_its_newest(app, world, ai):
    older, anchor = world.pk("ada-sent-01-lunch-reply.eml"), world.pk(LUNCH)
    a = app.post("/api/ai/draft-reply", {"pk": older}).json
    b = app.post("/api/ai/draft-reply", {"pk": anchor}).json
    assert len(drafts(ai)) == 1 and a["body"] == b["body"]
    assert peek(app, older)["status"] == "none"          # never paired with a composer quoting an older message
    assert peek(app, anchor)["status"] == "ready"


def test_hwf_a_pre_draft_rechecks_the_cache_after_winning_the_lead(app, world, ai):
    import server
    anchor = world.pk(LUNCH)
    assert app.post("/api/ai/draft-reply", {"pk": anchor}).status == 200
    ai.requests.clear()
    assert server._gen_draft_ahead(w.ADA, anchor) is None
    assert ai.requests == []


# -- the kick bar and banking ---------------------------------------------------------------------

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


def test_hwf_the_client_kick_bar_is_what_the_server_says(app, world, tmp_path):
    # The page has no copy of the rule. It reads `reply_worthy` off the row the server listed, and
    # that is the bar the kick route holds to, tested above.
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    with open(os.path.join(os.path.dirname(__file__), "..", "web", "assets", "legacy", "app.js"), encoding="utf-8") as f:
        fn = re.search(r"function _draftEligibleClient\(it\)\{[^{}]*\}", f.read()).group(0)
    pk = world.pk(QUILT)
    items = []
    for (importance, category, action), _ in KICK_BAR:
        summarize_as(pk, importance, category, action)
        [row] = [i for i in app.get(f"/api/messages?account={w.ADA}&folder=INBOX&limit=100").json["items"] if i["pk"] == pk]
        items.append(row)
    out = subprocess.run([node, "-e", fn + f"\nconsole.log(JSON.stringify({json.dumps(items)}"
                                           ".map(it => !!_draftEligibleClient(it))));"],
                         capture_output=True, text=True, timeout=20, cwd=tmp_path)
    assert json.loads(out.stdout) == [want for _, want in KICK_BAR], out.stderr


def test_hwf_a_warm_pre_draft_answers_the_draft_button_for_free(app, world, ai):
    pk = world.pk(QUILT)
    summarize_as(pk, 5, "personal")
    ai.queue(fa.answer({"body": "It looks wonderful, well done!"}))
    assert kick(app, pk)["status"] == "generating"
    app.settle()
    ai.requests.clear()
    r = app.post("/api/ai/draft-reply", {"pk": pk}).json
    assert (r["body"], r["model"]) == ("It looks wonderful, well done!", "claude-opus-5-5")
    assert ai.requests == []


def test_hwf_a_custom_draft_is_never_banked(app, world, ai):
    pk = world.pk(QUILT)
    for custom in ({"tone": "warm"}, {"instruction": "Say I can't make it"}):
        assert app.post("/api/ai/draft-reply", {"pk": pk, **custom}).status == 200
    assert len(drafts(ai)) == 2
    assert peek(app, pk)["status"] == "none"
    assert draft_rows(w.ADA, thread_key(world, QUILT)) == []


def test_hwf_a_failed_bank_write_still_returns_the_draft(app, world, ai, monkeypatch):
    import sqlite3
    import store

    def full_disk(*a, **kw):
        raise sqlite3.OperationalError("database or disk is full")
    monkeypatch.setattr(store, "draft_ahead_put", full_disk)
    r = app.post("/api/ai/draft-reply", {"pk": world.pk(QUILT)})
    assert r.status == 200 and r.json["body"]


# -- the draft-ahead tick -------------------------------------------------------------------------

@pytest.mark.parametrize("outcomes", [
    [False, None, None, False, None, False, True, True],     # a neutral None neither counts nor resets
    [False, False, True, False, False, False, True, True],   # a success resets the count
])
def test_hwf_the_tick_breaker_opens_after_three_failures_it_led(app, monkeypatch, present, outcomes):
    import server
    ran = []

    def gen(account, pk, source="dwell"):
        ran.append(pk)
        return outcomes[len(ran) - 1]
    monkeypatch.setattr(server, "_pick_draft_candidate", lambda: {"account": w.ADA, "pk": 1})
    monkeypatch.setattr(server, "_gen_draft_ahead", gen)
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
    for path in (f"/api/ai/draft-ahead?pk={world.pk(QUILT)}&peek=1", f"/api/folders?account={w.ADA}",
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
    import summarize
    from mailmind.bootstrap import assistant
    summarize_as(world.pk(QUILT), 5, "personal")
    with assistant.governor().call("interactive", "rewrite", summarize.MODEL, None):   # one in flight
        run_tick(app, monkeypatch, cycles=2)
        assert drafts(ai) == []
    run_tick(app, monkeypatch, cycles=1)                       # 120s after it ended
    assert len(drafts(ai)) == 1


# -- draft-ahead garbage collection ---------------------------------------------------------------

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
    assert app.post("/api/ai/draft-reply", {"pk": world.pk(LUNCH)}).status == 200      # banked
    key = thread_key(world, LUNCH)
    world.deliver(w.ADA, "Sent", w.variant("ada-sent-01-lunch-reply.eml", Message_ID="<lunch-4@ada.example.test>",
                                           Date="Mon, 02 Nov 2026 11:00:00 +0000"))
    [newest] = q("SELECT folder, answered FROM messages WHERE account=? AND thread_id=? ORDER BY ts DESC LIMIT 1",
                 w.ADA, key[2:])
    assert newest == {"folder": "Sent", "answered": 0}
    run_tick(app, monkeypatch, cycles=1)
    assert draft_rows(w.ADA, key) == []


def test_hwf_draft_gc_evicts_by_last_use_counting_a_regeneration_as_use(data_dir):
    import store
    now = int(time.time())
    c = store.conn()
    try:
        for i in range(498):
            put_draft(w.ADA, f"t:filler-{i}", created=now - 100, c=c)
        put_draft(w.ADA, "t:regenerated", created=now - 60, hit=now - 3000, c=c)     # rewritten a minute ago
        put_draft(w.ADA, "t:opened", created=now - 2600, hit=now - 5, c=c)           # old, but opened just now
        put_draft(w.ADA, "t:idle", created=now - 2500, c=c)                          # the least recently useful
        store.draft_ahead_gc(c, now, orphans=False)
        c.commit()
        left = {r["ref_key"] for r in c.execute("SELECT ref_key FROM draft_ahead")}
    finally:
        c.close()
    assert len(left) == 500 and "t:idle" not in left and {"t:regenerated", "t:opened"} <= left


# -- do-plan, snooze and the weekly review --------------------------------------------------------

def test_hwf_do_plan_counts_is_unread_as_narrowing_but_not_is_read(app, world, ai):
    plans = {}
    for flt in ("is:read", "is:unread"):
        ai.queue(fa.answer({"intent": "act", "verb": "archive", "filter": flt}))
        plans[flt] = app.post("/api/ai/do-plan", {"cmd": f"archive {flt}"}).json["intent"]
    assert plans == {"is:read": "search", "is:unread": "act"}


def test_hwf_do_plan_refuses_a_filter_that_matches_most_of_the_mailbox(app, world, ai):
    ai.queue(fa.answer({"intent": "act", "verb": "archive", "filter": "after:2020-01-01"}))
    r = app.post("/api/ai/do-plan", {"cmd": "archive everything since 2020"}).json
    assert r == {"ok": True, "intent": "search", "filter": "after:2020-01-01"}


def test_hwf_snooze_caps_a_bulk_pk_list_server_side(app, world):
    r = app.post("/api/snooze", {"pks": list(range(1, 601)), "preset": "tomorrow"})
    assert r.status == 200 and r.json["count"] == 500


def test_hwf_the_review_names_a_waiting_thread_by_its_thread_not_its_old_pk(app, world, ai):
    now = int(time.time())
    execute("INSERT INTO followups(account, thread_id, kind, state, anchor_pk, first_sent_ts, last_msg_ts, "
            "due_ts, created_at) VALUES(?,?,?,?,?,?,?,?,?)", w.CY, world.row(VENUE_END)["thread_id"],
            "sent", "due", 999999, now - 3 * 86400, now - 3 * 86400, now - 86400, "2026-10-30T12:00:00+00:00")
    r = app.post("/api/review", {}).json
    assert [x["subject"] for x in r["waiting"]] == ["Re: Guild meetup venue"]


def test_hwf_a_degenerate_review_falls_back_but_keeps_the_ai_picks(app, world, ai):
    ai.queue(fa.answer({"narrative": "test", "next": ["Answer Mae about the hall"]}))
    r = app.post("/api/review", {}).json
    assert r["model"] == "fallback" and r["narrative"].startswith("In the last 7 days you sent")
    assert r["next"] == ["Answer Mae about the hall"]


def test_without_a_key_triage_sends_no_mail(world, anthropic_api):
    import summarize
    summarize.summarize_pending(limit=3, workers=1)
    assert anthropic_api.requests == []


CACHED_READS = {     # each cached AI read, and the store read that finds its cache
    "brief": "get_brief", "delta": "get_delta", "review": "get_review",
    "thread summary": "get_thread_summary",
}


@pytest.mark.parametrize("which", list(CACHED_READS))
def test_two_first_loads_of_a_cached_ai_read_pay_once(app, world, ai, monkeypatch, which):
    import store
    summarize_as(world.pk(LUNCH), 5, "action-needed", "Reply")
    real = getattr(store, CACHED_READS[which])
    reads, held, go = [], threading.Event(), threading.Event()

    def slow_first_read(*a, **kw):
        row = real(*a, **kw)
        reads.append(row)
        if len(reads) == 1:                       # the first load found no cache: hold it there
            held.set()
            go.wait(5)
        return row
    monkeypatch.setattr(store, CACHED_READS[which], slow_first_read)
    first = threading.Thread(target=AI_READS[which], args=(app, world))
    first.start()
    held.wait(5)
    second = AI_READS[which](app, world)          # meanwhile a second load computes and caches it
    go.set()
    first.join(10)
    assert second.status == 200 and not first.is_alive()
    assert len(ai.requests) == 1


def test_a_thread_summary_stays_fresh_across_a_renumber(app, world, ai):
    assert app.post("/api/ai/summarize", {"pk": world.pk(LUNCH)}).status == 200
    renumber_inbox(world, w.ADA)
    r = app.post("/api/ai/summarize", {"pk": pk_of(LUNCH_MID)}).json
    assert r["cached"] is True
    assert r["stale"] is False


def test_a_brief_chip_counts_the_messages_it_holds(app, world, ai):
    anchor = world.pk(LUNCH)
    summarize_as(anchor, 5, "action-needed", "Reply")
    ai.queue(fa.answer({"narrative": "Lin is waiting on lunch plans for Thursday, so reply today.",
                        "groups": [{"label": "Lunch", "count": 9, "pks": [anchor, 999999]}]}))
    [group] = app.post("/api/briefing", {}).json["groups"]
    assert (group["count"], group["pks"]) == (1, [anchor])


def test_a_cached_brief_still_names_its_messages_after_a_renumber(app, world, ai):
    anchor = world.pk(LUNCH)
    summarize_as(anchor, 5, "action-needed", "Reply")
    ai.queue(fa.answer({"narrative": "Lin is waiting on lunch plans for Thursday, so reply today.",
                        "groups": [{"label": "Lunch", "count": 1, "pks": [anchor]}]}))
    assert app.post("/api/briefing", {}).json["groups"][0]["pks"] == [anchor]
    renumber_inbox(world, w.ADA)
    pks = [p for g in app.post("/api/briefing", {}).json["groups"] for p in g["pks"]]
    assert [m["message_id"] for p in pks for m in q("SELECT message_id FROM messages WHERE pk=?", p)] == [LUNCH_MID]


def test_a_message_without_a_thread_id_can_be_summarized(app, world, ai):
    pk = world.pk(QUILT)
    execute("UPDATE messages SET thread_id=NULL WHERE pk=?", pk)
    first = app.post("/api/ai/summarize", {"pk": pk})
    assert first.status == 200 and first.json["scope"] == "message" and len(ai.requests) == 1
    assert app.post("/api/ai/summarize", {"pk": pk}).json["cached"] is True     # and it finds its summary again
    assert len(ai.requests) == 1


def test_a_message_with_neither_thread_nor_message_id_can_be_summarized(app, world, ai):
    pk = world.pk(QUILT)
    execute("UPDATE messages SET thread_id=NULL, message_id='' WHERE pk=?", pk)
    assert app.post("/api/ai/summarize", {"pk": pk}).status == 200


def test_a_draft_survives_switching_the_from_account(app, world, ai):
    assert app.post("/api/ai/draft-reply", {"pk": world.pk(CLIENT), "account": w.ADA}).status == 200
    [asked] = ai.requests
    assert w.ADA in asked.prompt and "Contract draft v2" in asked.prompt     # Ada answers Bea's mail


def test_the_review_counts_only_this_accounts_cleanups(app, world, ai):
    import store
    c = store.conn()
    try:
        store.unsub_log_put(c, "news@loom.example.test", "post", "ok", 200,
                            datetime.now(timezone.utc).isoformat(), "unsub-loom-1", [w.ADA])
        c.commit()
    finally:
        c.close()
    assert app.post("/api/review", {"account": w.ADA}).json["stats"]["cleaned"] == 1
    assert app.post("/api/review", {"account": w.BEA}).json["stats"]["cleaned"] == 0    # Loom only ever wrote to Ada


def test_an_ai_spending_get_the_page_didnt_make_spends_nothing(app, world, ai):
    """These GETs only read what is stored, and only the page's own same-origin request may reach
    them: not one that says nothing of where it came from."""
    summarize_as(world.pk(LUNCH), 5, "action-needed", "Reply")
    for path in ("/api/briefing", "/api/review", "/api/briefing/since?since=0"):
        app.get(path, headers={"X-CSRF-Token": None, "Sec-Fetch-Site": None})
    assert [r.prompt[:40] for r in ai.requests] == []


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
