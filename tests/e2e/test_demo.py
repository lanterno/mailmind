"""`just demo` as a program: the app comes up on the fictional world and on nothing else, and takes its data with it."""
import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time

import guard

ROOT = guard.ROOT


def free_port():
    with socket.create_server(("127.0.0.1", 0)) as s:
        return s.getsockname()[1]


def get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def wait_for_mail(port, proc):
    deadline = time.monotonic() + 60
    while True:
        assert proc.poll() is None and time.monotonic() < deadline, "the demo's mail never came"
        try:
            status, body = get(port, "/api/inboxes?limit=100")
            items = json.loads(body)["items"] if status == 200 else []
            if len({i["account"] for i in items}) == 3:
                return items
        except OSError:
            pass
        time.sleep(0.1)


def test_the_demo_runs_on_the_fictional_world_and_leaves_nothing(tmp_path):
    port = free_port()
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "MAILMIND_HOME")}
    proc = subprocess.Popen([sys.executable, "tests/support/demo.py", str(port)], cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env={**env, "TMPDIR": str(tmp_path)})
    try:
        items = wait_for_mail(port, proc)
        assert all(i["account"].endswith("@example.test") for i in items)
        assert all(i["from_addr"].endswith((".test", ".example")) or "example" in i["from_addr"] for i in items)
        assert json.loads(get(port, "/api/digest")[1])["send_accounts"]
        assert [p.name for p in tmp_path.iterdir()][0].startswith("mailmind-demo-")
        proc.send_signal(signal.SIGINT)
        assert proc.wait(timeout=20) in (0, -signal.SIGINT, 130)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    out = proc.stdout.read() if proc.stdout else ""
    assert re.search(rf"MailMind → http://127\.0\.0\.1:{port}", out)
    assert "Traceback" not in out and "Tripwire" not in out, out
    assert list(tmp_path.iterdir()) == []
