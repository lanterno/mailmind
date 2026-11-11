"""Waiting for something a test can see, rather than for a while.

A test that waits for a worker polls for what the worker does. The deadline is generous, so a slow or busy machine only
makes the test slower; it fails only when the thing never happens. Where what a test waits for is another thread coming to
a stop (a job queued behind a gate, a reader waiting for an event), `blocked_in` says when it has.
"""
import sys
import threading
import time

_CONDITION_WAIT = threading.Condition.wait.__code__


def wait_until(cond, timeout=30.0):
    """Poll `cond` until it holds; True once it does, False if `timeout` seconds pass first."""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
    return True


def blocked_in(thread, function):
    """Whether `thread` is held inside `function`: on a lock it takes there, or a condition or event it waits on there."""
    frame = sys._current_frames().get(thread.ident)
    if frame is None or frame.f_code not in (_CONDITION_WAIT, function.__code__):
        return False
    while frame is not None and frame.f_code.co_filename == threading.__file__:
        frame = frame.f_back
    return frame is not None and frame.f_code is function.__code__
