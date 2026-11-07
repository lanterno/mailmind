"""Browser fixtures: one Chromium for the session, and per test a page on the app fixture's
server (the fake IMAP world, a temporary database, the frozen clock).

The page may reach only the app's own origin: any other request is answered with an empty response
here and listed in `page.blocked`, which `clean` holds empty, so no test sends anything off the machine. The page's clock is set to the server's NOW, so "today"
means the same day on both sides. `page.problems` collects uncaught errors, console errors and
content security policy violations.
"""
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone

import pytest

SANDBOX_REFUSAL = re.compile(r"Blocked script execution in '(about:srcdoc|about:blank|[^']*/api/message/\d+/body\.html\?[^']*)' because the "
                             r"document's frame is sandboxed and the 'allow-scripts' permission is not set\.")
# What Chromium says of anything a content security policy refuses.
CSP_VIOLATION = "violates the following Content Security Policy directive"


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """The React app, built once for the session into a folder of its own, so a build on this machine doesn't matter."""
    from mailmind.platform import webroot
    out = tmp_path_factory.mktemp("next-dist")
    web = os.path.join(webroot.REPO, "web")
    if not os.path.isdir(os.path.join(web, "node_modules")):
        pytest.skip("web/node_modules isn't installed")
    subprocess.run([shutil.which("npx"), "vite", "build", "--outDir", str(out), "--emptyOutDir"], cwd=web, check=True,
                   capture_output=True)
    return str(out)


@pytest.fixture
def serve_the_build(built, monkeypatch):
    from mailmind.platform import webroot
    monkeypatch.setattr(webroot, "DIST", built)


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
    from mailmind.platform import sse
    monkeypatch.setattr(sse, "HEARTBEAT", 0.5)            # a closed page's event stream ends quickly
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
        if SANDBOX_REFUSAL.match(m.text):
            return
        p.problems.append(f"console.{m.type}: {m.text}")

    p.pw.on("console", console)
    p.pw.set_default_timeout(10_000)
    try:
        yield p
    finally:
        ctx.close()
