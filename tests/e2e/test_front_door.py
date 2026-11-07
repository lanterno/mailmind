"""`python -m mailmind serve` as a program, run end to end.

It runs in a child process with the tripwire installed and an empty temp data directory, read-only
(MAILMIND_PROFILE), with no Claude key.
"""
import contextlib
import http.client
import json
import os
import re
import signal
import socket
import stat
import subprocess
import sys
import time

import guard

ROOT = guard.ROOT
TESTS = os.path.join(ROOT, "tests")

# The child: the tripwire first, then the program.
GUARDED = ("import sys; sys.path[:0] = [{tests!r}, {root!r}]; import guard; guard.install(); "
           "import runpy; sys.argv = {argv!r}; runpy.run_module('mailmind', run_name='__main__')")


def child_env(home):
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    return {**env, "MAILMIND_HOME": str(home), "MAILMIND_PROFILE": "read-only",
            "PYTHONUNBUFFERED": "1"}


def free_port():
    with socket.create_server(("127.0.0.1", 0)) as s:
        return s.getsockname()[1]


def get(port, path, timeout=2):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def test_serve_answers_streams_and_stops_at_once_on_ctrl_c(tmp_path):
    """It ends as interrupted (by SIGINT, so a caller such as `just` stops too) and quietly."""
    port = free_port()
    code = GUARDED.format(tests=TESTS, root=ROOT, argv=["mailmind", "serve", str(port)])
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=child_env(tmp_path),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    stream = None
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                status, body = get(port, "/api/outbox")
                break
            except OSError:
                assert proc.poll() is None and time.monotonic() < deadline, "serve didn't come up"
                time.sleep(0.05)
        assert (status, body) == (200, b'{"ok": true, "items": [], "drafts": []}')
        assert sorted(p.name for p in tmp_path.iterdir())[0] == "mailmind.db"
        stream = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        stream.request("GET", "/api/events", headers={"Host": f"127.0.0.1:{port}",
                                                      "Sec-Fetch-Site": "same-origin"})
        r = stream.getresponse()
        assert (r.status, r.readline()) == (200, b"retry: 3000\n")
        began = time.monotonic()
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=10) == -signal.SIGINT
        assert time.monotonic() - began < 2
        r.read()                                         # the stream ended, not cut off
    finally:
        if stream is not None:
            stream.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    out = proc.stdout.read() if proc.stdout else ""
    assert f"MailMind → http://127.0.0.1:{port}" in out
    assert "[imap] mail through imaplib" in out          # no account here asks for IMAPClient
    assert "Traceback" not in out and "Tripwire" not in out, out


@contextlib.contextmanager
def serving(home):
    """The app running over the data directory `home`, until the block ends: its port."""
    port = free_port()
    code = GUARDED.format(tests=TESTS, root=ROOT, argv=["mailmind", "serve", str(port)])
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=child_env(home),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                get(port, "/api/outbox")
                break
            except OSError:
                assert proc.poll() is None and time.monotonic() < deadline, "serve didn't come up"
                time.sleep(0.05)
        yield port
    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def post(port, path, body):
    """A POST as the page sends it, with the CSRF token this run serves in its page."""
    token = re.search(rb'<meta name="csrf" content="([^"]+)"', get(port, "/")[1]).group(1).decode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("POST", path, body=json.dumps(body), headers={
            "Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json", "X-CSRF-Token": token})
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


def test_it_keeps_its_log_in_the_data_directory_its_owners_alone(tmp_path):
    with serving(tmp_path) as port:
        pass
    path = tmp_path / "mailmind.log"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert f"MailMind → http://127.0.0.1:{port}  (serving now)" in [line["msg"] for line in lines]
    assert "mail through imaplib" in [line["msg"] for line in lines if line["logger"] == "imap"]


def test_undo_outlives_a_restart(world):
    """Archived in one run, undone in the next. Both run read-only, so the archive waits in the queue and its Undo asks
    no server."""
    from fixtures import world as w
    receipt = world.pk("ada-inbox-05-receipt.eml")
    with serving(world.dir) as port:
        assert post(port, "/api/archive", {"pk": receipt})["status"] == "queued"
    with serving(world.dir) as port:
        r = post(port, "/api/undo", {})
        assert (r["ok"], r["label"], r["remaining"]) == (True, "Archived", 0)
        listed = json.loads(get(port, f"/api/messages?account={w.ADA}&limit=200")[1])["items"]
        assert receipt in [it["pk"] for it in listed]
