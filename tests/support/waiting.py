"""Waiting for something a test can see, rather than for a while.

A test that waits for a worker polls for what the worker does. The deadline is generous, so a slow or busy machine only
makes the test slower; it fails only when the thing never happens.
"""
import time


def wait_until(cond, timeout=30.0):
    """Poll `cond` until it holds; True once it does, False if `timeout` seconds pass first."""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
    return True
