"""Golden files: a test records what the app answered, and the record must match the file under
tests/golden/ exactly. `pytest --update-golden` rewrites the files instead; review the diff
before committing it, because that diff *is* the behaviour change.

Values that differ from run to run for reasons the test doesn't control (the server's port, the
temp data directory) are replaced with placeholders before comparison. The clock is frozen by
the `app` fixture, so timestamps are stable and stay in the record.
"""
import hashlib
import json
import os
import re

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

# Response headers worth pinning: what a browser acts on. Length and date are left out.
HEADERS = ("content-type", "content-disposition", "cache-control", "x-frame-options",
           "x-content-type-options", "referrer-policy", "content-security-policy")


def _file_for(nodeid):
    module, _, name = nodeid.partition("::")
    stem = os.path.splitext(os.path.basename(module))[0].removeprefix("test_")
    name = re.sub(r"[^\w.-]+", "_", name.removeprefix("test_")).strip("_")
    return os.path.join(GOLDEN_DIR, stem, name + ".json")


class Golden:
    def __init__(self, nodeid, update, subs, ports=None):
        self.path = _file_for(nodeid)
        self.update = update
        self.subs = sorted(subs, key=lambda s: -len(s[0]))   # [(literal, placeholder)]
        self.ports = ports or {}                             # {port number: placeholder}
        self.entries = []

    def _norm(self, v, key=""):
        if isinstance(v, str):
            for lit, ph in self.subs:
                v = v.replace(lit, ph)
            return v
        if isinstance(v, int) and not isinstance(v, bool) and "port" in key.lower() and v in self.ports:
            return self.ports[v]                             # a port number under a *port* key
        if isinstance(v, list):
            return [self._norm(x, key) for x in v]
        if isinstance(v, dict):
            return {self._norm(k): self._norm(x, str(k)) for k, x in v.items()}
        return v

    def record(self, label, resp, *, body=True):
        """Record one exchange: its label, status, the headers in HEADERS and the body: parsed
        JSON as it is, short text as text, anything else as its length and sha256. body=False
        leaves a non-JSON body out (the page, whose every edit would otherwise show up here)."""
        entry = {"request": label, "status": resp.status,
                 "headers": {k: resp.headers[k] for k in HEADERS if k in resp.headers}}
        ctype = resp.headers.get("content-type", "")
        if isinstance(resp.body, (dict, list)):
            entry["body"] = resp.body
        elif not body:
            pass
        elif ctype.startswith("text/") and len(resp.body) < 8192:
            entry["text"] = resp.body.decode("utf-8", "replace")
        else:
            entry["bytes"] = len(resp.body)
            entry["sha256"] = hashlib.sha256(resp.body).hexdigest()
        self.entries.append(self._norm(entry))
        return resp

    def note(self, label, value):
        """Record any other value the test wants pinned (a database row, the server's commands)."""
        self.entries.append(self._norm({"note": label, "value": value}))

    def verify(self):
        text = json.dumps(self.entries, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        if self.update:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(text)
            return
        if not os.path.exists(self.path):
            raise AssertionError(f"no golden file {os.path.relpath(self.path)}; run pytest --update-golden")
        with open(self.path, encoding="utf-8") as f:
            want = json.load(f)
        assert json.loads(text) == want, f"differs from {os.path.relpath(self.path)}"
