"""The trace: a step a line, text first (test-architecture.md section 9).

Each step is what a scenario did in a person's words (a screen's phrase, or the client's own verb when it acts alone),
the client's actions inside it, the requests the page made, the core's events it applied, what the fakes saw (IMAP
commands that change mail, messages sent), what changed on screen, and how long the step and its settling took. A
failing test writes it to build/traces/<test>/ as trace.jsonl and trace.txt, beside the last `shown`, a picture and the
server's log.
"""
import contextlib
import json
import os
import re
import shutil
import time

from .world import ROOT

TRACES = os.path.join(ROOT, "build", "traces")


def folder_for(nodeid):
    """build/traces/<the test's file and name>, as a folder name."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid.replace("tests/scenarios/", "").replace(".py::", "--"))
    return os.path.join(TRACES, name[:180])


class Trace:
    def __init__(self, world, client=None):
        self.world, self.client = world, client
        self.steps = []
        self._open = None
        self.typed = []                    # what was typed, in order: the oracle's history, when it comes

    @contextlib.contextmanager
    def step(self, phrase):
        """A step: the actions inside it are one line of the trace, and its `shown` diff is taken once, at its end."""
        if self._open is not None:
            self._open["actions"].append(phrase)
            yield
            return
        started = time.monotonic()
        record = {"n": len(self.steps) + 1, "phrase": phrase, "actions": [], "settle_ms": 0.0}
        self._open = record
        mark = self.world.mark()
        if self.client is not None:
            self.client.begin_step()
        failed = None
        try:
            yield record
        except BaseException as e:
            failed = e
            raise
        finally:
            self._open = None
            record.update(self.world.since(mark))
            if self.client is not None:
                record.update(self.client.end_step())
                record["settle_ms"] = round(record["settle_ms"], 1)
            if failed is not None:
                record["failed"] = f"{type(failed).__name__}: {str(failed).splitlines()[0] if str(failed) else ''}"
            record["ms"] = round((time.monotonic() - started) * 1000, 1)
            self.steps.append(record)

    def settled(self, ms):
        if self._open is not None:
            self._open["settle_ms"] += ms

    def world_step(self, phrase):
        with self.step(phrase):
            pass

    @property
    def phrases(self):
        return [s["phrase"] for s in self.steps]

    def slowest(self, n=5):
        return sorted(self.steps, key=lambda s: s["ms"], reverse=True)[:n]

    # ── written out ─────────────────────────────────────────────────────

    def text(self):
        out = []
        for s in self.steps:
            out.append(f"{s['n']}. {s['phrase']}  ({s['ms']:.0f} ms, settling {s['settle_ms']:.0f} ms)")
            if s["actions"]:
                out.append("   did: " + "; ".join(s["actions"]))
            for key, label in (("requests", "requests"), ("events", "core events"), ("imap", "imap"), ("smtp", "smtp")):
                if s.get(key):
                    out.append(f"   {label}: " + "; ".join(s[key]))
            if s.get("failed"):
                out.append(f"   FAILED: {s['failed']}")
            if s.get("shown"):
                out += ["   shown:"] + [f"     {line}" for line in s["shown"].splitlines()]
        return "\n".join(out)

    def write(self, folder, shown=None, picture=None):
        """The trace, the last `shown`, a picture and the server's log, in `folder`, made afresh."""
        shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "trace.jsonl"), "w", encoding="utf-8") as f:
            for s in self.steps:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        with open(os.path.join(folder, "trace.txt"), "w", encoding="utf-8") as f:
            f.write(self.text() + "\n")
        if shown is not None:
            with open(os.path.join(folder, "shown.txt"), "w", encoding="utf-8") as f:
                f.write(shown + "\n")
        if picture:
            shutil.copyfile(picture, os.path.join(folder, "picture.png"))
        log = getattr(self.world.app, "log_path", None)
        if log and os.path.exists(log):
            shutil.copyfile(log, os.path.join(folder, "server.log"))
        return folder
