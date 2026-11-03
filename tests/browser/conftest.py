"""Browser fixtures: one Chromium for the session, and per test a page on the app fixture's
server (the fake IMAP world, a temporary database, the frozen clock).

The page may reach only the app's own origin: any other request (the web fonts dashboard.html
asks Google for) is answered with an empty response here and listed in `page.blocked`, so no
test sends anything off the machine. The page's clock is set to the server's NOW, so "today"
means the same day on both sides. `page.problems` collects uncaught errors, console errors and
content security policy violations.
"""
from datetime import datetime, timezone

import pytest

SANDBOX_REFUSAL = ("Blocked script execution in 'about:srcdoc' because the document's frame is sandboxed "
                   "and the 'allow-scripts' permission is not set.")
# What Chromium says of anything a content security policy refuses, or would refuse: a report-only
# policy's violations come at the info level, not as errors.
CSP_VIOLATION = "violates the following Content Security Policy directive"


@pytest.fixture(scope="session")
def chromium():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


class Page:
    """A Playwright page plus what the fence and the console saw."""

    def __init__(self, page, origin):
        self.pw, self.origin = page, origin
        self.blocked, self.problems = [], []

    def __getattr__(self, name):
        return getattr(self.pw, name)


@pytest.fixture
def page(chromium, app, monkeypatch):
    import realtime
    monkeypatch.setattr(realtime, "HEARTBEAT", 0.5)       # a closed page's event stream ends quickly
    origin = f"http://127.0.0.1:{app.port}"
    ctx = chromium.new_context(viewport={"width": 1440, "height": 900}, locale="en-US", timezone_id="UTC",
                               base_url=origin)
    p = Page(ctx.new_page(), origin)

    def fence(route):
        url = route.request.url
        if url.startswith(origin + "/") or url.startswith(("data:", "blob:")):
            route.continue_()
        else:
            p.blocked.append(url)
            route.fulfill(status=200, body="")

    ctx.route("**/*", fence)
    p.pw.clock.install(time=datetime.now(timezone.utc))     # the app fixture froze it at NOW
    p.pw.on("pageerror", lambda e: p.problems.append(f"pageerror: {e}"))

    def console(m):
        # The fake clock injects a script into every frame, and Chromium refuses it in a mail
        # body's frame (sandboxed without allow-scripts), saying so on the console. That refusal
        # is the sandbox working, not a fault in the page.
        if m.type != "error" and CSP_VIOLATION not in m.text:
            return
        if m.location.get("url") == "about:srcdoc" and m.text.startswith(SANDBOX_REFUSAL):
            return
        p.problems.append(f"console.{m.type}: {m.text}")

    p.pw.on("console", console)
    p.pw.set_default_timeout(10_000)
    try:
        yield p
    finally:
        ctx.close()
