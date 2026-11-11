"""A backlog of triage through the Message Batches API: what each transport sends and reads, what a batch
costs and how that counts against the daily limit, and the job that sends one and brings its answers in.
Everything here is against the fake API; nothing real is sent."""
import time

import pytest

from fakes import anthropic_api as fa
from fixtures import world as w
from mailmind.assistant.domain.prompts import SYSTEM
from mailmind.bootstrap.assistant import answers
from mailmind.bootstrap import assistant
from mailmind.bootstrap import background
from mailmind.bootstrap import wiring

TRANSPORTS = ["sdk", "urllib"]


@pytest.fixture(params=TRANSPORTS)
def transport(request, data_dir, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.status, "_last", {"ok": None, "ts": 0, "error": ""})
    monkeypatch.setattr(assistant.calls, "MODEL", None)
    monkeypatch.delenv("MAILMIND_NO_SCHEMA", raising=False)
    monkeypatch.delenv("MAILMIND_MODEL", raising=False)
    assistant.use(request.param)
    yield request.param
    assistant.use("urllib")


@pytest.fixture
def gw(transport, ai):
    from mailmind.bootstrap import assistant
    return assistant.gateway()


def posts(ai):
    return [r for r in ai.requests if r.method == "POST" and r.path.startswith(fa.BATCHES)]


# -- the job ---------------------------------------------------------------------

@pytest.fixture
def backlog(world, ai, monkeypatch):
    """The world's mail, all waiting for triage, as a backlog from 5 messages up, with 2 kept back."""
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.assessments, "BATCH_MIN", 5)
    monkeypatch.setattr(assistant.assessments, "BATCH_KEEP_NEWEST", 2)
    monkeypatch.setattr(assistant.assessments, "BATCH_POLL", 0)
    monkeypatch.setattr(assistant.calls, "MODEL", None)
    return world


def _waiting():
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return answers.needs_summary(c, 1000)
    finally:
        c.close()


def _sql(q, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    try:
        return c.execute(q, args).fetchall()
    finally:
        c.close()


def _write(q, *args):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    c.execute(q, args)
    c.commit()
    c.close()


def _sent(ai):
    return [int(r["custom_id"]) for b in ai.batches.values() for r in b["requests"]]


def test_a_backlog_goes_as_a_batch_and_the_newest_stay(backlog, ai):
    from mailmind.bootstrap import assistant
    before = _waiting()
    n = assistant.assessments.batch_submit()
    assert n == len(before) - 2
    assert _sent(ai) == [r["message_key"] for r in before[2:]]
    assert [r["message_key"] for r in _waiting()] == [r["message_key"] for r in before[:2]]


def test_a_few_new_messages_are_no_backlog(backlog, ai, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.assessments, "BATCH_MIN", len(_waiting()) + 1)
    assert assistant.assessments.batch_submit() == 0
    assert not posts(ai)


def test_one_batch_at_a_time(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    assert assistant.assessments.batch_submit() == 0
    assert len(posts(ai)) == 1


def test_a_batch_is_no_bigger_than_its_maximum(backlog, ai, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.assessments, "BATCH_MAX", 3)
    assert assistant.assessments.batch_submit() == 3


def test_while_a_batch_is_out_the_rest_of_the_backlog_waits_and_only_new_mail_goes_now(backlog, ai, monkeypatch):
    """The old mail a batch didn't take goes in the next one; paying full price for it would defeat the point."""
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.assessments, "BATCH_MAX", 3)
    before = _waiting()
    assert assistant.assessments.batch_submit() == 3
    assert assistant.assessments.summarize_pending(limit=1000) == 2     # the two kept back, and none of the older rest
    assert len([r for r in ai.requests if r.path == "/v1/messages"]) == 2
    backlog.deliver(w.ADA, "INBOX", w.variant("ada-inbox-02-lunch-confirm.eml", Message_ID="<fresh@example.test>",
                                              Date="Mon, 02 Nov 2026 11:59:00 +0000"))
    assert assistant.assessments.summarize_pending(limit=1000) == 1     # new mail doesn't wait
    assert len(_waiting()) == len(before) - 3 - 2


def test_answers_reach_every_copy_and_the_key(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    key = _sent(ai)[0]
    row = _sql("SELECT * FROM messages WHERE message_key=?", key)[0]
    _write("INSERT INTO messages(account, kind, folder, uid, message_id, ts, from_addr, subject, is_junk, message_key) "
           "VALUES (?,?,'Archive',990,?,?,?,?,0,?)", row["account"], row["kind"], row["message_id"], row["ts"],
           row["from_addr"], row["subject"], key)
    sent = len(_sent(ai))
    ai.end_batch(results={str(key): fa.succeeded({"summary": "Said once.", "importance": 9, "category": "info", "action": ""})})
    assert assistant.assessments.batch_collect() == sent
    got = _sql("SELECT a.summary, a.importance FROM messages m JOIN assessments a ON a.message_key=m.message_key WHERE m.message_key=?", key)
    assert [tuple(r) for r in got] == [("Said once.", 5), ("Said once.", 5)]    # importance clamped, as ever


def test_an_answer_for_a_message_whose_copies_went_is_kept_under_its_key(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    key = _sent(ai)[0]
    _write("DELETE FROM messages WHERE message_key=?", key)
    ai.end_batch(results={str(key): fa.succeeded({"summary": "Comes back.", "importance": 2, "category": "info", "action": ""})})
    assistant.assessments.batch_collect()
    assert _sql("SELECT summary FROM assessments WHERE message_key=?", key)[0][0] == "Comes back."


def test_an_answer_for_nothing_of_ours_is_ignored(backlog, ai):
    from mailmind.bootstrap import assistant
    ours = assistant.assessments.batch_submit()
    assert ours > 0
    batch = list(ai.batches)[-1]
    ai.batches[batch]["requests"].append({"custom_id": "not-a-key", "params": {}})
    ai.batches[batch]["requests"].append({"custom_id": "999999", "params": {}})
    ai.end_batch()
    assert assistant.assessments.batch_collect() == ours


def test_a_reply_with_nothing_usable_stores_the_fallback(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    key = _sent(ai)[0]
    ai.end_batch(results={str(key): fa.succeeded("not json at all")})
    assistant.assessments.batch_collect()
    row = _sql("SELECT model FROM assessments WHERE message_key=?", key)[0]
    assert row["model"] == "parse-error"


def test_a_message_with_no_answer_waits_again(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    sent = _sent(ai)
    a, b, c_ = sent[:3]
    ai.end_batch(results={str(a): fa.EXPIRED, str(b): fa.errored("overloaded_error"), str(c_): fa.CANCELED})
    assistant.assessments.batch_collect()
    waiting = {r["message_key"] for r in _waiting()}
    assert {a, b, c_} <= waiting
    assert len(sent) > 3 and not (set(sent[3:]) & waiting)


def test_nothing_is_brought_in_before_the_batch_ends(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    assert assistant.assessments.batch_collect() == 0
    assert assistant.assessments.batch_status()["waiting"] > 0
    assert not ai.deleted


def test_it_asks_only_so_often(backlog, ai, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.assessments, "BATCH_POLL", 300)
    assert assistant.assessments.batch_submit() > 0
    ai.requests.clear()
    assistant.assessments.batch_collect()
    assert ai.requests == []
    _write("UPDATE ai_batches SET polled_at = polled_at - 301")
    assistant.assessments.batch_collect()
    assert len(ai.requests) == 1
    assistant.assessments.batch_collect()
    assert len(ai.requests) == 1


def test_a_batch_is_removed_from_the_api_once_its_answers_are_stored(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    batch = list(ai.batches)[-1]
    ai.end_batch()
    assistant.assessments.batch_collect()
    assert ai.deleted == [batch]
    assert [r["state"] for r in _sql("SELECT state FROM ai_batches")] == ["done"]


def test_removing_it_is_tried_again_when_it_fails(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    batch = list(ai.batches)[-1]
    ai.end_batch()
    real = ai._batch
    ai._batch = lambda m, rest, req: fa.error(400, "invalid_request_error", "no") if m == "DELETE" else real(m, rest, req)
    assistant.assessments.batch_collect()
    assert not ai.deleted and [r["state"] for r in _sql("SELECT state FROM ai_batches")] == ["collected"]
    ai._batch = real
    assert assistant.assessments.batch_collect() == 0       # nothing is stored twice
    assert ai.deleted == [batch] and [r["state"] for r in _sql("SELECT state FROM ai_batches")] == ["done"]


def test_a_batch_the_api_no_longer_has_gives_its_messages_back(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    ai.batches.clear()
    assistant.assessments.batch_collect()
    assert _sql("SELECT * FROM ai_batches") == [] and assistant.assessments.batch_status()["waiting"] == 0


def test_an_outage_leaves_the_batch_out(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    ai.queue(*[fa.error(503, "overloaded_error", "busy")] * 3)
    assert assistant.assessments.batch_collect() == 0
    assert assistant.assessments.batch_status()["state"] == "running"
    ai.end_batch()
    assert assistant.assessments.batch_collect() > 0


def test_a_batch_that_wasnt_sent_gives_its_messages_back(backlog, ai):
    from mailmind.bootstrap import assistant
    before = _waiting()
    ai.queue(fa.error(500, "api_error", "no"))
    assert assistant.assessments.batch_submit() == 0
    assert _sql("SELECT * FROM ai_batches") == []
    assert len(_waiting()) == len(before)
    assert assistant.assessments.batch_submit() > 0         # the next round tries again


def test_a_crash_while_sending_gives_its_messages_back_in_time(backlog, ai):
    from mailmind.bootstrap.mailbox import store
    from mailmind.bootstrap import assistant
    c = store.conn()
    keys = [r["message_key"] for r in _waiting()[:2]]
    answers.batch_begin(c, "triage-batch", "claude-opus-5-5", keys, 10, 0, int(time.time()))
    c.commit()
    c.close()
    assistant.assessments.batch_collect()
    assert assistant.assessments.batch_status()["waiting"] == 2     # it may still be going
    assert assistant.assessments.batch_submit() == 0
    _write("UPDATE ai_batches SET created_at = created_at - ?", assistant.assessments.BATCH_SEND_GIVE_UP + 1)
    assistant.assessments.batch_collect()
    assert assistant.assessments.batch_status()["waiting"] == 0


def test_a_restart_finds_the_batch_it_left(backlog, ai):
    """All of this is in the database, so a new process goes on from it, through either transport."""
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    ai.end_batch()
    assistant.use("sdk")
    try:
        assert assistant.assessments.batch_collect() > 0
    finally:
        assistant.use("urllib")


# -- within the daily limit ------------------------------------------------------

def _limit(data_dir_path, usd):
    import json
    with open(data_dir_path / "mailmind.config.json") as f:
        cfg = json.load(f)
    cfg["ai_daily_limit_usd"] = usd
    with open(data_dir_path / "mailmind.config.json", "w") as f:
        json.dump(cfg, f)


def test_a_batch_takes_only_its_share_of_what_the_limit_leaves(backlog, ai):
    from mailmind.bootstrap import assistant
    from mailmind.assistant.domain import pricing
    rows = _waiting()[2:]
    each = [pricing.batch_estimate("claude-opus-5-5", len(SYSTEM) + len(assistant.assessments._triage_prompt(r))) for r in rows]
    _limit(backlog.dir, (sum(each[:3]) + each[3] // 2) * assistant.assessments.BATCH_SHARE / 1_000_000)
    assert assistant.gateway().room() // assistant.assessments.BATCH_SHARE >= sum(each[:3])
    assert assistant.assessments.batch_submit() == 3


def test_what_a_batch_books_counts_the_system_prompt_each_request_carries(backlog, ai):
    from mailmind.bootstrap import assistant
    from mailmind.assistant.domain import pricing
    rows = _waiting()[assistant.assessments.BATCH_KEEP_NEWEST:]
    assert assistant.assessments.batch_submit(at_least=1) == len(rows) > 0
    assert assistant.assessments.batch_status()["estimate_micros"] == sum(
        pricing.batch_estimate("claude-opus-5-5", len(SYSTEM) + len(assistant.assessments._triage_prompt(r))) for r in rows)


def test_at_the_limit_no_batch_goes(backlog, ai):
    from mailmind.bootstrap import assistant
    from mailmind.assistant.application.ports import Spend, Usage
    from mailmind.assistant.application.gateway import local_day
    assistant.LEDGER.record(Spend(local_day(time.time()), "ask", "claude-opus-5-5", "claude-opus-5-5", "ok", Usage(),
                                  assistant.limit_micros(), int(time.time())))
    assert assistant.assessments.batch_submit() == 0
    assert not posts(ai)


def test_a_limit_with_no_room_for_one_request_sends_nothing(backlog, ai):
    from mailmind.bootstrap import assistant
    _limit(backlog.dir, 0.000001)
    assert assistant.assessments.batch_submit() == 0
    assert not posts(ai)


def test_what_a_batch_may_cost_holds_back_background_work_but_not_someone_asking(backlog, ai):
    from mailmind.bootstrap import assistant
    gw = assistant.gateway()
    assert assistant.assessments.batch_submit() > 0
    booked = assistant.assessments.batch_status()["estimate_micros"]
    assert assistant.governor().snapshot()["reserved_micros"] == booked
    _limit(backlog.dir, booked / 1_000_000)
    assert gw.held_back("triage") == "limit"
    assert gw.held_back("ask") is None
    assert gw.spent_today() == 0                # booked isn't spent
    assert gw.ask("hello", feature="ask").text == fa.STUB_TEXT


def test_what_it_booked_is_released_when_its_answers_are_in(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    ai.end_batch()
    assistant.assessments.batch_collect()
    assert assistant.governor().snapshot()["reserved_micros"] == 0


def test_answers_are_brought_in_at_the_limit_and_while_someone_uses_ai(backlog, ai, monkeypatch):
    """They are paid for already, and bringing them in sends no mail."""
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    ai.end_batch()
    monkeypatch.setattr(assistant.gateway(), "held_back", lambda *a, **k: "yielding")
    assert assistant.assessments.round() == assistant.assessments.ASSESS_YIELD
    assert assistant.assessments.batch_status()["waiting"] == 0
    assert ai.deleted


# -- the round, and AI off -------------------------------------------------------

def test_the_round_sends_the_backlog_and_triages_the_newest_at_once(backlog, ai):
    before = _waiting()
    assistant.assessments.round()
    assert len(posts(ai)) == 1
    assert len([r for r in ai.requests if r.path == "/v1/messages"]) == 2
    assert len(_waiting()) == 0 and len(_sent(ai)) == len(before) - 2


def test_the_switch_keeps_the_round_from_sending_one_and_still_brings_one_in(backlog, ai, monkeypatch):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    ai.end_batch()
    monkeypatch.setenv("MAILMIND_NO_BATCHES", "1")
    ai.requests.clear()
    assistant.assessments.round()
    assert ai.deleted                           # the one that was out came in
    assert not posts(ai)                        # and no other went, though mail is waiting


def test_with_ai_off_nothing_goes_and_nothing_is_touched(world, anthropic_api):
    from mailmind.bootstrap import assistant
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    keys = [r["message_key"] for r in answers.needs_summary(c, 2)]
    answers.batch_begin(c, "triage-batch", "claude-opus-5-5", keys, 10, 0, int(time.time()))
    answers.batch_sent(c, 1, "msgbatch_x", 0)
    c.commit()
    c.close()
    assert assistant.assessments.batch_submit(at_least=1, keep=0) == 0
    assert assistant.assessments.batch_collect() == 0
    assert anthropic_api.requests == []
    assert assistant.assessments.batch_status()["waiting"] == 2


def test_status_says_so_only_when_a_batch_is_out(backlog, ai):
    from mailmind.bootstrap import assistant
    assert "batch_waiting" not in assistant.status.report()
    n = assistant.assessments.batch_submit()
    assert assistant.status.report()["batch_waiting"] == n
    ai.end_batch()
    assistant.assessments.batch_collect()
    assert "batch_waiting" not in assistant.status.report()


def test_finished_batches_are_forgotten_after_a_month(backlog, ai):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    ai.end_batch()
    assistant.assessments.batch_collect()
    background.forget_round()
    assert len(_sql("SELECT * FROM ai_batches")) == 1
    _write("UPDATE ai_batches SET ended_at = ended_at - ?", 31 * 86400)
    background.forget_round()
    assert _sql("SELECT * FROM ai_batches") == []


# -- the command -----------------------------------------------------------------

def test_the_command_sends_any_size_and_says_so(backlog, ai, capsys, monkeypatch):
    from mailmind.bootstrap import assistant
    monkeypatch.setattr(assistant.assessments, "BATCH_MIN", 100000)
    assert wiring.assess_batch() == 0
    assert len(posts(ai)) == 1
    assert "are out in a batch" in capsys.readouterr().out


def test_the_command_brings_in_a_batch_that_ended(backlog, ai, capsys):
    from mailmind.bootstrap import assistant
    assert assistant.assessments.batch_submit() > 0
    ai.end_batch()
    assert wiring.assess_batch() == 0
    assert "brought" in capsys.readouterr().out
    assert ai.deleted


def test_the_command_says_when_nothing_is_waiting(backlog, ai, capsys):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    for r in answers.needs_summary(c, 1000):
        answers.save_summary(c, r["pk"], "s", 2, "info", "", "m", "2026-11-02T12:00:00+00:00")
    c.commit()
    c.close()
    assert wiring.assess_batch() == 0
    assert "Nothing was sent" in capsys.readouterr().out and not posts(ai)


def test_the_command_with_ai_off_sends_nothing(world, anthropic_api, capsys):
    assert wiring.assess_batch() == 1
    assert "AI is off" in capsys.readouterr().out and anthropic_api.requests == []


def test_the_command_in_the_read_only_profile_sends_nothing(backlog, ai, capsys, monkeypatch):
    from mailmind.platform import profile
    monkeypatch.setattr(profile, "writes_allowed", lambda: False)
    assert wiring.assess_batch() == 1
    assert "read-only" in capsys.readouterr().out and not ai.requests


def test_the_command_line_sends_a_batch_when_asked(backlog, ai):
    from mailmind.__main__ import main
    with pytest.raises(SystemExit) as e:
        main(["assess", "--batch"])
    assert e.value.code == 0 and len(posts(ai)) == 1
