"""Browser tests that must patch the app's insides, which only the app in the test's own process allows: a sanitizer made
to let a script through, to show the mail frame's sandbox still stops it, or a timing constant shortened. The rest of the
browser suite runs against the server as a program, which nothing here reaches into.
"""
import pytest


@pytest.fixture
def app(in_process_app):
    return in_process_app


@pytest.fixture(autouse=True)
def serve_the_build(built, monkeypatch):
    """The app exists only as built, so every test here serves that build."""
    from mailmind.platform import webroot
    monkeypatch.setattr(webroot, "DIST", built)


@pytest.fixture(autouse=True)
def _quick_keepalive(monkeypatch):
    """A closed page's event stream ends at the next keepalive, so that comes quickly."""
    from mailmind.platform import sse
    monkeypatch.setattr(sse, "HEARTBEAT", 0.5)
