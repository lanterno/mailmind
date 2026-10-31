"""Send-later times on /api/send (server.do_send): a passed, far-off or malformed send_after is
refused before anything is built, a sane one is scheduled, and none means send now. Hermetic —
temp database and config, and the outbox is replaced with a recorder, so nothing can leave.
All addresses are fictional.
Run: python3 test_send_after.py"""
import json
import os
import tempfile
import time

import store
import config
_TMP = tempfile.mkdtemp()
store.DB = os.path.join(_TMP, "mailmind.db")        # before anything opens a connection
config.CONFIG = os.path.join(_TMP, "mailmind.config.json")
with open(config.CONFIG, "w") as f:
    json.dump({"accounts": [{"email": "ada@example.test", "type": "imap",
                             "host": "imap.example.test"}]}, f)
import server

ACCT = "ada@example.test"
_real_spec = server._compose_spec


def _fresh():
    store.DB = os.path.join(tempfile.mkdtemp(), "mailmind.db")
    store.init()
    calls = []

    def enqueue(built, meta):
        calls.append(meta)
        return 1, True
    server.outbox.enqueue = enqueue
    server._compose_spec = _real_spec
    return calls


def _body(send_after):
    return {"mode": "new", "account": ACCT, "to": ["bo@example.test"], "subject": "Plans",
            "text": "See you Saturday.", "send_after": send_after}


def _refuses(send_after, error):
    calls = _fresh()

    def boom(body):
        raise AssertionError("_compose_spec ran for a refused send time")
    server._compose_spec = boom
    res, code = server.do_send(_body(send_after))
    assert (code, res) == (400, {"ok": False, "error": error}), (code, res)
    assert calls == [], calls


def test_passed_time_is_refused_before_building():
    _refuses(int(time.time()) - 60, "that send time has passed")


def test_more_than_a_year_out_is_refused():
    _refuses(int(time.time()) + 400 * 86400, "pick a send time within a year")


def test_malformed_time_is_refused():
    _refuses("soon", "bad send time")


def test_future_time_is_scheduled():
    calls = _fresh()
    sa = int(time.time()) + 3600
    res, code = server.do_send(_body(sa))
    assert code == 200 and res["ok"] and res["scheduled"] is True, (code, res)
    assert len(calls) == 1 and calls[0]["send_after"] == sa, calls


def test_no_time_sends_now():
    calls = _fresh()
    for sa in (0, None):
        res, code = server.do_send(_body(sa))
        assert code == 200 and res["scheduled"] is False, (code, res)
    assert [m["send_after"] for m in calls] == [0, 0], calls


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
        except Exception as e:
            failed += 1
            print("ER:U", fn.__name__, repr(e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
