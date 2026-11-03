"""The two front doors as programs: `python -m mailmind serve`, the new one, run end to end, and
`python3 server.py`, the old one, kept free of third-party imports so it runs with nothing
installed. Every app test also runs through both doors (the app fixture's door=old and door=new).

Each program runs in a child process with the tripwire installed and an empty temp data
directory, read-only (MAILMIND_PROFILE), with no Claude key.
"""
import http.client
import os
import signal
import socket
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
    return {**env, "MAILMIND_HOME": str(home), "MAILMIND_PROFILE": "read-only", "MAILMIND_AI": "api",
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


def test_the_old_front_door_imports_no_third_party_package(tmp_path):
    """python3 server.py is the kill switch, so it must run on a bare Python."""
    code = ("import sys; before = set(sys.modules); import server; "
            "print(' '.join(sorted({m.split('.')[0] for m in set(sys.modules) - before})))")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=child_env(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    ours = {p[:-3] for p in os.listdir(ROOT) if p.endswith(".py")} | {"mailmind"}
    loaded = set(r.stdout.split())
    assert "server" in loaded
    third_party = {m for m in loaded - ours - set(sys.stdlib_module_names)
                   if not m.startswith("_sysconfigdata")}           # the stdlib's per-platform data
    assert third_party == set()


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
        assert (status, body) == (200, b'{"ok": true, "items": []}')
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
    assert "Traceback" not in out and "Tripwire" not in out, out
