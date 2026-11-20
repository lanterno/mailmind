"""The live event stream, /api/events: what a listener receives, in the server-sent-events framing,
how a tab that reconnects resumes, and how the stream ends. The golden files are in
tests/golden/http_events/, each event's id written <run>-<n>.

The stream is read over a raw socket, as EventSource would (support/event_stream.py). A server keeps its own keepalive
interval, cap on listeners and count of events kept for tabs that reconnect; the tests that cut those to something a test
reaches are in tests/characterization/test_http_events_in_process.py.
"""
import json
import re
from datetime import timedelta

from event_stream import CONNECTION_HEADERS, Stream
from fixtures import world as w
from mailmind.platform.sse import MAX_LISTENERS
from waiting import wait_until

READ_THREAD = "ada-inbox-04-ci.eml"                     # muting it queues nothing: no `outbox` event among these
BUILD_META = re.compile(r'<meta name="mailmind-build" content="([^"]+)"')


def listening(app):
    """How many streams the server holds open. Only its cap shows that: MAX_LISTENERS less the streams it lets open now
    before it turns one away. Those hang up as it returns."""
    opened = []
    try:
        while len(opened) < MAX_LISTENERS:
            s = Stream(app)
            if s.status != 200:
                s.close()
                break
            opened.append(s)
        return MAX_LISTENERS - len(opened)
    finally:
        for s in opened:
            s.close()


def listening_once_let_go(app):
    """How many it holds once it has let go of the tabs that hung up, which it notices a moment after."""
    counts = []
    wait_until(lambda: counts.append(listening(app)) or counts[-1] == 0)
    return counts[-1]


def run_of(frame):
    """The run an event's id names: <run>-<n>, drawn by the server as it starts."""
    return frame.split("id: ")[1].split("\n")[0].rpartition("-")[0]


def test_a_stream_answers_as_an_event_stream(app, golden):
    # What EventSource holds a server to: the type, no caching, and the first frame telling it how long to wait.
    s = Stream(app)
    golden.note("status and headers", {"status": s.status, "headers": {k: v for k, v in s.headers.items()
                                                                       if k not in CONNECTION_HEADERS}})
    golden.note("first frame", s.frame())
    s.close()


def test_every_listener_hears_an_event(app, golden, world):
    a, b = Stream(app), Stream(app)
    for s in (a, b):
        s.frame()
    app.post("/api/mute", {"account": w.CY, "thread_id": world.row("cy-inbox-01-thread-start.eml")["thread_id"]})
    golden.note("what each heard", [a.event(), b.event()])
    for s in (a, b):
        s.close()
    golden.note("listeners once both hang up", listening_once_let_go(app))


def test_a_cross_site_listener_is_refused(app, golden):
    golden.record("cross-site", app.get("/api/events", headers={"Sec-Fetch-Site": "cross-site"}))
    golden.record("another host", app.get("/api/events", headers={"Host": "mail.example.test"}))
    golden.note("listeners", listening(app))


# -- a tab that reconnects ------------------------------------------------------

def test_a_tab_that_never_heard_an_event_resumes_from_where_it_opened(app, golden, world):
    s = Stream(app)
    opened = s.frame()
    s.close()
    app.post("/api/mute", world.copy(READ_THREAD))
    t = Stream(app, **{"Last-Event-ID": opened.split("\nid: ")[1].split("\n")[0]})
    golden.note("it opened with", opened)
    golden.note("and on reconnecting hears", [t.frame(), t.frame()])
    t.close()


def test_a_tab_from_before_a_restart_is_told_to_fetch_afresh(app, golden, world):
    s = Stream(app)
    s.frame()
    app.post("/api/mute", world.copy(READ_THREAD))
    heard = s.event()
    last = heard.split("\n")[0].removeprefix("id: ")
    s.close()
    app.restart()
    t = Stream(app, **{"Last-Event-ID": last})
    reconnected = [t.frame(), t.frame()]
    t.close()
    # The restarted server draws a run of its own, which the file names apart from the first.
    assert run_of(reconnected[0]) != run_of(heard)
    golden.alias(run_of(reconnected[0]), "<later run>")
    golden.note("it reconnects and hears", reconnected)


# -- what tells the open tabs ---------------------------------------------------

def test_a_send_that_goes_out_tells_the_open_tabs(app, world):
    s = Stream(app)
    s.frame()
    queued = app.post("/api/send", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Linen",
                                    "text": "Half is yours.", "idempotency_key": "k-tabs"}).json
    app.clock.shift(timedelta(seconds=queued["undo_seconds"] + 1))
    app.work("outbox")
    assert s.event().split("\n")[1:] == ["event: outbox", "data: {}"]
    s.close()


def test_a_send_that_fails_tells_the_open_tabs(app, world):
    s = Stream(app)
    s.frame()
    world.smtp.fail("RCPT", "451 4.3.0 try again later")
    queued = app.post("/api/send", {"account": w.ADA, "mode": "new", "to": ["lin@example.test"], "subject": "Linen",
                                    "text": "Half is yours.", "idempotency_key": "k-fails"}).json
    app.clock.shift(timedelta(seconds=queued["undo_seconds"] + 1))
    app.work("outbox")
    assert s.event().split("\n")[1:] == ["event: outbox", "data: {}"]
    s.close()


def test_a_queued_move_the_server_refuses_tells_the_open_tabs(app, world):
    s = Stream(app)
    s.frame()
    world.server.drop("SELECT", user=w.ADA)
    assert app.post("/api/archive", {**world.copy("ada-inbox-05-receipt.eml"),
                                     "mutation_id": "m-no"}).json["status"] == "queued"
    assert s.event().split("\n")[1:] == ["event: outbox", "data: {}"]         # the tray shows it
    world.server.fail("UID MOVE", user=w.ADA)
    world.server.fail("UID COPY", user=w.ADA)
    app.work("commands")
    assert s.event().split("\n")[1:] == ["event: commands-changed", "data: {}"]
    s.close()


def test_a_queued_move_that_lands_tells_the_open_tabs(app, world):
    s = Stream(app)
    s.frame()
    world.server.drop("SELECT", user=w.ADA)
    r = app.post("/api/archive", {**world.copy("ada-inbox-05-receipt.eml"), "mutation_id": "m-tabs"}).json
    assert r["status"] == "queued"
    assert s.event().split("\n")[1:] == ["event: outbox", "data: {}"]         # the tray shows it
    app.work("commands")
    assert s.event().split("\n")[1:] == ["event: commands-changed", "data: {}"]
    s.close()


def test_a_conversation_filed_within_the_request_tells_the_open_tabs_once(app, world):
    # Its moves landed before the answer, so no worker tells of them: the request does, once for the conversation, and a
    # client that reads its counts again on the stream doesn't keep them stale.
    s = Stream(app)
    s.frame()
    said = app.post("/api/thread/archive", world.copy("ada-inbox-02-lunch-confirm.eml")).json
    assert (said["msg"], said["count"], said["queued"]) == ("filed", 2, 0)
    assert s.event().split("\n")[1:] == ["event: commands-changed", "data: {}"]
    app.post("/api/mute", world.copy(READ_THREAD))       # what is told next: nothing came between
    assert s.event().split("\n")[1] == "event: mute"
    s.close()


def test_a_message_filed_within_the_request_tells_the_open_tabs(app, world):
    s = Stream(app)
    s.frame()
    assert app.post("/api/archive", world.copy("ada-inbox-05-receipt.eml")).json["msg"] == "archived"
    assert s.event().split("\n")[1:] == ["event: commands-changed", "data: {}"]
    app.post("/api/mute", world.copy(READ_THREAD))
    assert s.event().split("\n")[1] == "event: mute"
    s.close()


def test_a_senders_cleanup_that_sweeps_within_the_request_tells_the_open_tabs_once(app, world):
    # It trashes their mail a message at a time, as a click on each would, and tells of them all once.
    for n in (1, 2):
        world.deliver(w.ADA, "INBOX", w.variant("ada-inbox-03-newsletter.eml", Message_ID=f"<issue-{300 + n}@loom.example.test>"))
    s = Stream(app)
    s.frame()
    said = app.post("/api/cleanup/execute", {"from_addr": "news@loom.example.test", "do_unsubscribe": False}).json
    assert (said["swept"], said["sweep_queued"]) == (3, 0)
    assert s.event().split("\n")[1:] == ["event: commands-changed", "data: {}"]
    app.post("/api/mute", world.copy(READ_THREAD))
    assert s.event().split("\n")[1] == "event: mute"
    s.close()


def test_the_end_of_a_folders_first_scan_tells_the_open_tabs(app, world):
    s = Stream(app)
    s.frame()
    world.server.deliver(w.ADA, "Junk", w.raw("ada-inbox-12-scam.eml"))
    assert len(app.get(f"/api/messages?account={w.ADA}&folder=Junk").json["items"]) == 1
    head, data = s.event().split("\n")[1:]
    assert head == "event: folder-synced"
    assert json.loads(data.removeprefix("data: ")) == {"account": w.ADA, "folder": "Junk", "ok": True, "new": 1}
    world.server.fail("SELECT", user=w.ADA)
    assert app.get(f"/api/messages?account={w.ADA}&folder=Spam").json["sync_failed"] is True
    head, data = s.event().split("\n")[1:]
    assert json.loads(data.removeprefix("data: ")) == {"account": w.ADA, "folder": "Spam", "ok": False, "new": 0}
    s.close()


def test_every_stream_names_the_build_being_served(app, world):
    tab = Stream(app)
    assert tab.frame().startswith("retry:")
    app.post("/api/mute", world.copy(READ_THREAD))      # an event, so the frame read after the hello comes
    tab.event()
    tab.close()
    # The build the page it served names, which a tab compares with what it loaded under.
    build = BUILD_META.search(app.get("/").body.decode("utf-8")).group(1)
    assert tab.hello == f'event: hello\ndata: {{"build": "{build}"}}'


def test_new_mail_names_the_one_message_it_brought(app, world):
    # One new message names its UID, so a notification can say whose it is and open it; more than one is a count.
    s = Stream(app)
    s.frame()
    one = world.server.deliver(w.ADA, "INBOX", w.variant("ada-inbox-05-receipt.eml", Message_ID="<one@example.test>"))
    for n in (1, 2):
        world.server.deliver(w.CY, "INBOX", w.variant("cy-inbox-03-unrelated.eml", Message_ID=f"<two-{n}@example.test>"))
    app.post("/api/refresh", {})
    heard = {}
    while len(heard) < 2:
        head, data = s.event().split("\n")[1:]
        if head == "event: new-mail":
            said = json.loads(data.removeprefix("data: "))
            heard[said["account"]] = said
    assert heard == {w.ADA: {"account": w.ADA, "folder": "INBOX", "count": 1, "uid": one},
                     w.CY: {"account": w.CY, "folder": "INBOX", "count": 2}}
    s.close()


def test_new_mail_names_the_message_it_kept_not_a_newer_one_that_was_junk(app, world):
    # Junk stays in the Inbox with a badge, and isn't new mail: one message kept and a newer junk one in the same round
    # name the one kept.
    s = Stream(app)
    s.frame()
    kept = world.server.deliver(w.ADA, "INBOX", w.variant("ada-inbox-05-receipt.eml", Message_ID="<kept@example.test>"))
    junk = world.server.deliver(w.ADA, "INBOX", w.variant("ada-inbox-12-scam.eml", Message_ID="<junk@prizes.example.test>"))
    assert junk > kept
    app.post("/api/refresh", {})
    while True:
        head, data = s.event().split("\n")[1:]
        if head == "event: new-mail":
            break
    assert json.loads(data.removeprefix("data: ")) == {"account": w.ADA, "folder": "INBOX", "count": 1, "uid": kept}
    s.close()


def test_a_round_of_summaries_tells_the_open_tabs_how_many_landed(app, world, ai):
    # Summaries change what needs you with no mail changing, so nothing else would say so: the lists, the Inbox's tabs,
    # the rail and the brief read them again on this. A round that summarized nothing says nothing.
    waiting = app.get("/api/digest").json["ai"]["summaries_waiting"]
    assert waiting > 0
    s = Stream(app)
    s.frame()
    app.work("assess")
    head, data = s.event().split("\n")[1:]
    assert (head, json.loads(data.removeprefix("data: "))) == ("event: summarized", {"count": waiting})
    app.work("assess")
    app.post("/api/mute", world.copy(READ_THREAD))       # what is told next: nothing came between
    assert s.event().split("\n")[1] == "event: mute"
    s.close()
