"""Send-later times on /api/send (sending.send): a passed, far-off or malformed send_after is refused
before anything is built, a sane one is scheduled, and none means send now. The outbox is replaced
with a recorder, so nothing can leave. All addresses are fictional."""
import json
import time

import pytest

import problems
from answered import answered
from mailmind.bootstrap import compose
from mailmind.platform import settings

ACCT = "ada@example.test"
CONFIG = {"accounts": [{"email": ACCT, "type": "imap", "host": "imap.example.test"}]}


@pytest.fixture
def queued(data_dir, monkeypatch):
    """What reached the outbox: each message's meta."""
    with open(settings.current().config, "w") as f:
        json.dump(CONFIG, f)
    calls = []

    def enqueue(built, meta):
        calls.append(meta)
        return 1, True
    monkeypatch.setattr(compose.outbox, "enqueue", enqueue)
    return calls


def _body(send_after):
    return {"mode": "new", "account": ACCT, "to": ["bo@example.test"], "subject": "Plans",
            "text": "See you Saturday.", "send_after": send_after}


@pytest.mark.parametrize("send_after, error", [
    pytest.param(lambda now: now - 60, "that send time has passed", id="passed"),
    pytest.param(lambda now: now + 400 * 86400, "pick a send time within a year", id="more_than_a_year_out"),
    pytest.param(lambda now: "soon", "bad send time", id="malformed"),
])
def test_a_bad_time_is_refused_before_building(send_after, error, queued, monkeypatch):
    def boom(body):
        raise AssertionError("_compose_spec ran for a refused send time")
    monkeypatch.setattr(compose.composing, "spec", boom)
    res, code = answered(compose.sending.send, _body(send_after(int(time.time()))))
    assert (code, res) == (400, problems.refused(error))
    assert queued == []


def test_future_time_is_scheduled(queued):
    sa = int(time.time()) + 3600
    res, code = answered(compose.sending.send, _body(sa))
    assert code == 200 and res["ok"] and res["scheduled"] is True, (code, res)
    assert len(queued) == 1 and queued[0]["send_after"] == sa


def test_no_time_sends_now(queued):
    for sa in (0, None):
        res, code = answered(compose.sending.send, _body(sa))
        assert code == 200 and res["scheduled"] is False, (code, res)
    assert [m["send_after"] for m in queued] == [0, 0]
