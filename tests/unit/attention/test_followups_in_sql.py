"""The minute's round runs as SQL, built from what domain.followups names. These hold the two to the same answer: each
condition and boundary by name, then random threads through the statements and through the rules."""
import itertools
import os

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from mailmind.attention.domain import followups
from mailmind.attention.domain.followups import DISMISSED, DUE, PENDING, RECEIVED, REPLY_LATER, SENT, FollowUp, Mail, Thread
from mailmind.bootstrap import attention

RUNS = int(os.environ.get("MAILMIND_FOLLOWUP_RUNS", "0"))
CASES = settings(max_examples=RUNS or 150, deadline=None, derandomize=not RUNS,
                 suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow])

ADA = "ada@example.test"
LIN = "lin@example.test"
NOW = 1_800_000_000
DAY = 86400
GRACE = followups.GRACE_DAYS * DAY
LIFE = followups.EXPIRE_DAYS * DAY
TIMES = [NOW, NOW - DAY, NOW - GRACE + 1, NOW - GRACE, NOW - GRACE - 1, NOW - LIFE + 1, NOW - LIFE, NOW - LIFE - 1,
         NOW - 3 * LIFE]
_ids = itertools.count(1)


@pytest.fixture
def db(data_dir):
    from mailmind.bootstrap.mailbox import store
    c = store.conn()
    yield c
    c.close()


def stored(c, thread_id, messages, muted=False, follow_up=None):
    """The thread in the database as these describe it, emptied of what came before. The Thread, with the pks it got."""
    from mailmind.bootstrap.mailbox import store
    for table in ("followups", "muted", "snoozes", "assessments", "messages"):
        c.execute(f"DELETE FROM {table}")
    out = []
    for m in messages:
        n = next(_ids)
        pk = store.upsert_message(c, {"account": ADA, "kind": "imap", "folder": "INBOX", "uid": n,
                                      "message_id": f"<{n}@example.test>", "gmail_thread": None,
                                      "from_addr": ADA.upper() if m.mine else LIN, "from_name": "", "subject": "Hello",
                                      "date_iso": None, "ts": m.ts, "snippet": "", "bucket": "other/personal-ish",
                                      "is_junk": int(m.junk)})
        c.execute("UPDATE messages SET thread_id=?, draft=?, seen=?, answered=? WHERE pk=?",
                  (thread_id, int(m.draft), int(m.seen), int(m.answered), pk))
        key = c.execute("SELECT message_key FROM messages WHERE pk=?", (pk,)).fetchone()[0]
        if m.snoozed:
            c.execute("INSERT INTO snoozes(message_key, folder, until) VALUES(?,?,?)", (key, "INBOX", NOW + DAY))
        c.execute("INSERT INTO assessments(message_key, summary, importance, category, action, model, created_at) "
                  "VALUES(?,?,?,?,?,?,?)", (key, "", 5 if m.reply_worthy else 1, "info", "", "m", "now"))
        out.append(Mail(pk, m.ts, m.mine, m.draft, m.seen, m.answered, m.junk, m.snoozed, m.reply_worthy))
    if muted:
        attention.book.mute_thread(c, ADA, thread_id, "now")
    if follow_up:
        c.execute("INSERT INTO followups(account, thread_id, kind, state, first_sent_ts, last_msg_ts) VALUES(?,?,?,?,?,?)",
                  (ADA, thread_id, follow_up.kind, follow_up.state, follow_up.first_sent_ts, follow_up.last_msg_ts))
    return Thread(thread_id, tuple(out), muted, follow_up)


def row(c):
    return c.execute("SELECT * FROM followups WHERE account=?", (ADA,)).fetchone()


def detect(c):
    attention.book.followup_detect_received(c, NOW, followups.GRACE_DAYS, followups.EXPIRE_DAYS)


# ── the statements are built from the domain's names ──────────────────────────
@pytest.mark.parametrize("rule, sql", [
    (followups.RECEIVED_NUDGE, attention.book._RECEIVED_NUDGE),
    (followups.COMES_DUE, attention.book._COMES_DUE),
    (followups.EXPIRES, attention.book._EXPIRES),
    (followups.MET, attention.book._MET),
], ids=["received-nudge", "comes-due", "expires", "met"])
def test_sql_says_each_condition_the_domain_names_and_no_other(rule, sql):
    assert sql.keys() == rule.keys()


# ── a condition at a time ─────────────────────────────────────────────────────
ASK = {"ts": NOW - 5 * DAY, "reply_worthy": True}
NUDGE_CASES = {
    "wanted": (True, "T", [Mail(0, **ASK)]),
    "wanted, at the end of the grace": (True, "T", [Mail(0, ts=NOW - GRACE, reply_worthy=True)]),
    "wanted, at the floor": (True, "T", [Mail(0, ts=NOW - LIFE, reply_worthy=True)]),
    "wanted, a draft of mine is no reply": (True, "T", [Mail(0, **ASK), Mail(0, ts=NOW - DAY, mine=True, draft=True)]),
    "wanted, a draft is not the newest": (True, "T", [Mail(0, **ASK), Mail(0, ts=NOW - DAY, draft=True, seen=False)]),
    "wanted, on an older message's verdict": (True, "T", [Mail(0, ts=NOW - 9 * DAY, reply_worthy=True), Mail(0, ts=NOW - 5 * DAY)]),
    "no thread id": (False, None, [Mail(0, **ASK)]),
    "an empty thread id": (False, "", [Mail(0, **ASK)]),
    "unread": (False, "T", [Mail(0, seen=False, **ASK)]),
    "answered": (False, "T", [Mail(0, answered=True, **ASK)]),
    "junk": (False, "T", [Mail(0, junk=True, **ASK)]),
    "within the grace": (False, "T", [Mail(0, ts=NOW - GRACE + 1, reply_worthy=True)]),
    "past the floor": (False, "T", [Mail(0, ts=NOW - LIFE - 1, reply_worthy=True)]),
    "the newest is too new": (False, "T", [Mail(0, **ASK), Mail(0, ts=NOW - DAY)]),
    "snoozed": (False, "T", [Mail(0, snoozed=True, **ASK)]),
    "no verdict that matters": (False, "T", [Mail(0, ts=NOW - 5 * DAY)]),
    "I wrote": (False, "T", [Mail(0, **ASK), Mail(0, ts=NOW - 6 * DAY, mine=True)]),
    "all drafts": (False, "T", [Mail(0, draft=True, **ASK)]),
}


@pytest.mark.parametrize("wanted, thread_id, messages", NUDGE_CASES.values(), ids=NUDGE_CASES.keys())
def test_a_nudge_both_ways(db, wanted, thread_id, messages):
    t = stored(db, thread_id, messages)
    detect(db)
    want, got = followups.nudge_for(t, NOW), row(db)
    assert (want is not None) is wanted
    assert (got["anchor_pk"], got["last_msg_ts"]) == (want.pk, want.ts) if wanted else got is None


def test_a_nudge_is_due_from_when_it_is_found(db):
    stored(db, "T", [Mail(0, **ASK)])
    detect(db)
    assert (row(db)["kind"], row(db)["state"], row(db)["due_ts"]) == (RECEIVED, DUE, NOW)


def test_a_muted_thread_gets_no_nudge_either_way(db):
    t = stored(db, "T", [Mail(0, **ASK)], muted=True)
    detect(db)
    assert followups.nudge_for(t, NOW) is None and row(db) is None


@pytest.mark.parametrize("kind", [SENT, RECEIVED, REPLY_LATER])
@pytest.mark.parametrize("state", [PENDING, DUE, DISMISSED])
def test_a_thread_with_a_follow_up_gets_no_second_either_way(db, kind, state):
    t = stored(db, "T", [Mail(0, **ASK)], follow_up=FollowUp(kind, state))
    detect(db)
    assert followups.nudge_for(t, NOW) is None
    assert (row(db)["kind"], row(db)["state"]) == (kind, state)


def test_mine_is_read_without_regard_to_case(db):
    t = stored(db, "T", [Mail(0, mine=True, **ASK)])        # stored upper-cased
    detect(db)
    assert followups.nudge_for(t, NOW) is None and row(db) is None


WAITING = {"kind": SENT, "state": PENDING, "first_sent_ts": NOW - GRACE}
MINE = Mail(0, ts=NOW - GRACE, mine=True)
DUE_CASES = {
    "comes due": (True, WAITING, [MINE], False),
    "more of mine is no answer": (True, WAITING, [MINE, Mail(0, ts=NOW - DAY, mine=True)], False),
    "still within the grace": (False, {**WAITING, "first_sent_ts": NOW - GRACE + 1}, [MINE], False),
    "no send time": (False, {**WAITING, "first_sent_ts": None}, [MINE], False),
    "answered since": (False, WAITING, [MINE, Mail(0, ts=NOW - GRACE + 1)], False),
    "muted": (False, WAITING, [MINE], True),
    "already due": (False, {**WAITING, "state": DUE}, [MINE], False),
    "dismissed": (False, {**WAITING, "state": DISMISSED}, [MINE], False),
    "a pin": (False, {**WAITING, "kind": REPLY_LATER}, [MINE], False),
    "a nudge": (False, {**WAITING, "kind": RECEIVED}, [MINE], False),
}


@pytest.mark.parametrize("due, follow_up, messages, muted", DUE_CASES.values(), ids=DUE_CASES.keys())
def test_coming_due_both_ways(db, due, follow_up, messages, muted):
    f = FollowUp(**follow_up)
    t = stored(db, "T", messages, muted, f)
    attention.book.followup_promote_due(db, NOW, followups.GRACE_DAYS)
    assert followups.comes_due(f, t, NOW) is due
    assert (row(db)["state"], row(db)["due_ts"]) == ((DUE, NOW) if due else (f.state, None))


EXPIRE_CASES = {
    "past its lifetime": (True, FollowUp(RECEIVED, DUE, last_msg_ts=NOW - LIFE - 1)),
    "pending, past its lifetime": (True, FollowUp(RECEIVED, PENDING, last_msg_ts=NOW - LIFE - 1)),
    "at its lifetime": (False, FollowUp(RECEIVED, DUE, last_msg_ts=NOW - LIFE)),
    "of unknown age": (False, FollowUp(RECEIVED, DUE, last_msg_ts=None)),
    "dismissed": (False, FollowUp(RECEIVED, DISMISSED, last_msg_ts=NOW - LIFE - 1)),
    "a sent one": (False, FollowUp(SENT, DUE, last_msg_ts=NOW - LIFE - 1)),
    "a pin": (False, FollowUp(REPLY_LATER, DUE, last_msg_ts=NOW - LIFE - 1)),
}


@pytest.mark.parametrize("gone, f", EXPIRE_CASES.values(), ids=EXPIRE_CASES.keys())
def test_an_expiry_both_ways(db, gone, f):
    stored(db, "T", [], follow_up=f)
    attention.book.expire_followups(db, NOW, followups.EXPIRE_DAYS)
    assert followups.expired(f, NOW) is gone
    assert (row(db) is None) is gone


SENT_DAY_AGO = FollowUp(SENT, DUE, first_sent_ts=NOW - DAY)
THEIRS, MY_REPLY, MY_DRAFT, ANSWERED = Mail(0, ts=NOW), Mail(0, ts=NOW, mine=True), Mail(0, ts=NOW, mine=True, draft=True), Mail(0, ts=NOW, answered=True)
MET_CASES = {
    "muted": (True, FollowUp(RECEIVED, DUE), [], True),
    "they answered": (True, SENT_DAY_AGO, [THEIRS], False),
    "they wrote, no send time": (True, FollowUp(SENT, DUE), [THEIRS], False),
    "they wrote at the send": (False, SENT_DAY_AGO, [Mail(0, ts=NOW - DAY)], False),
    "my own mail on a sent one": (False, SENT_DAY_AGO, [MY_REPLY], False),
    "I replied to a nudge": (True, FollowUp(RECEIVED, DUE), [MY_REPLY], False),
    "I replied to a pin": (True, FollowUp(REPLY_LATER, DUE), [MY_REPLY], False),
    "answered, a nudge": (True, FollowUp(RECEIVED, DUE), [ANSWERED], False),
    "answered, a pin": (True, FollowUp(REPLY_LATER, DUE), [ANSWERED], False),
    "a draft, on a nudge": (False, FollowUp(RECEIVED, DUE), [MY_DRAFT], False),
    "a draft, on a pin": (False, FollowUp(REPLY_LATER, DUE), [MY_DRAFT], False),
    "their mail, on a nudge": (False, FollowUp(RECEIVED, DUE), [THEIRS], False),
    "dismissed, muted": (False, FollowUp(SENT, DISMISSED), [], True),
    "dismissed, I replied": (False, FollowUp(RECEIVED, DISMISSED), [MY_REPLY], False),
    "dismissed, they answered": (False, FollowUp(SENT, DISMISSED), [THEIRS], False),
}


@pytest.mark.parametrize("gone, f, messages, muted", MET_CASES.values(), ids=MET_CASES.keys())
def test_a_commitment_met_both_ways(db, gone, f, messages, muted):
    t = stored(db, "T", messages, muted, f)
    attention.book.resolve_followups(db, ADA)
    assert followups.met(f, t) is gone
    assert (row(db) is None) is gone


@pytest.mark.parametrize("kind", [SENT, RECEIVED, REPLY_LATER])
def test_a_round_leaves_a_dismissal_where_it_is(db, kind):
    """Decision D12, through every statement a round runs and a send: it is still there, still dismissed."""
    dismissed = FollowUp(kind, DISMISSED, first_sent_ts=NOW - LIFE - 1, last_msg_ts=NOW - LIFE - 1)
    stored(db, "T", [Mail(0, reply_worthy=True, ts=NOW - 5 * DAY)], False, dismissed)
    book = attention.book
    book.resolve_followups(db, ADA)
    book.expire_followups(db, NOW, followups.EXPIRE_DAYS)
    book.followup_promote_due(db, NOW, followups.GRACE_DAYS)
    detect(db)
    book.followup_record_sent(db, ADA, "T", None, NOW, "now")
    got = row(db)
    assert (got["kind"], got["state"], got["first_sent_ts"]) == (kind, DISMISSED, NOW - LIFE - 1)


# ── any thread at all ─────────────────────────────────────────────────────────
likely = st.sampled_from([True, True, True, True, False])      # a rule asks a dozen things, so most hold or none gets far
unlikely = likely.map(lambda b: not b)
mail = st.builds(Mail, pk=st.just(0), ts=st.sampled_from(TIMES), mine=unlikely, draft=unlikely, seen=likely,
                 answered=unlikely, junk=unlikely, snoozed=unlikely, reply_worthy=likely)
any_follow_up = st.builds(FollowUp, kind=st.sampled_from([SENT, RECEIVED, REPLY_LATER]),
                          state=st.sampled_from([PENDING, DUE, DISMISSED]),
                          first_sent_ts=st.one_of(st.none(), st.sampled_from(TIMES)),
                          last_msg_ts=st.one_of(st.none(), st.sampled_from(TIMES)))
threads = st.tuples(st.sampled_from(["T", "T", "T", "T", "", None]), st.lists(mail, min_size=1, max_size=4), unlikely)


@CASES
@given(thread=threads, tracked=st.one_of(st.none(), st.none(), st.none(), any_follow_up))
def test_a_nudge_on_any_thread(db, thread, tracked):
    thread_id, messages, muted = thread
    if tracked and not thread_id:
        tracked = None                                  # a follow-up is kept by its thread
    t = stored(db, thread_id, messages, muted, tracked)
    detect(db)
    want, got = followups.nudge_for(t, NOW), row(db)
    if want is None:
        assert (got is None) == (tracked is None)
    else:
        assert (got["kind"], got["state"], got["anchor_pk"], got["last_msg_ts"], got["due_ts"]) == (RECEIVED, DUE, want.pk, want.ts, NOW)


@CASES
@given(thread=threads, f=any_follow_up)
def test_coming_due_on_any_thread(db, thread, f):
    thread_id, messages, muted = thread
    t = stored(db, thread_id or "T", messages, muted, f)
    attention.book.followup_promote_due(db, NOW, followups.GRACE_DAYS)
    assert row(db)["state"] == (DUE if followups.comes_due(f, t, NOW) else f.state)


@CASES
@given(thread=threads, f=any_follow_up)
def test_a_commitment_met_on_any_thread(db, thread, f):
    thread_id, messages, muted = thread
    t = stored(db, thread_id or "T", messages, muted, f)
    attention.book.resolve_followups(db, ADA)
    assert (row(db) is None) == followups.met(f, t)
