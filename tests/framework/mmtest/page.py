"""The page client: Playwright on the real build, in Chromium, served by `mailmind-server` (test-architecture.md 5.3).

It implements the client interface the screens call (`press`, `type`, `click`, `find`, `shown`, `settle`, `picture`).
Each action is real input, then a settle.

**The page's clock stands still.** It is installed at the world's time and paused, so nothing in the page moves on its
own: a toast stays until the test moves the clock past its life, as on the core. Settling moves it through short delays
(two seconds or less: a debounce, coalescing events, the 400 ms before the digest is read again, React Query's own
`setTimeout(0)`s) at once, and a long one passes only when the scenario says `world.clock.advance(...)`. A delay is
short by what it was set for, not by what is left of it, and the clock stands while a request is out.

**Settled** means all of these hold at once, on three probes in a row (section 7):

1. The page has applied every event the core published: the last event id its stream's listeners ran is the core's
   high-water id (`events`, from mm-served).
2. Nothing is in flight: no fetch the page made (counted in the page) and no other request (counted by Playwright), the
   event stream aside.
3. No short delay is pending in the page, and no short animation or transition is running (each is finished at once).
4. The server has no request still being answered (`settle`), so no route left work behind.
5. No job the app kicked is left: once 1 to 4 hold, a pass of each kicked job runs (`work kicked`), as its lane would
   have run it then, and settling goes on while that publishes events. Jobs otherwise stay paused (mm-served runs one
   only when asked), and the fakes answer only the server, so they are quiet once this holds.
"""
import os
import re
import tempfile
import time

from .shown import READ, Shown

SHORT_MS = 2_000             # a delay this short counts as the app being busy, and settling runs it
SETTLE_S = 20.0              # a step that can't settle in this long fails, saying what was still busy
RUN_AHEAD_MS = 30_000        # nor can one that keeps setting short delays past this much of the page's time
QUIET_PROBES = 3
EVENTS = "/api/events"

# Every frame of the page: counts what is in flight and what is pending, and which event its stream applied last.
# The page's clock is installed first, so the setTimeout wrapped here is the clock's own.
HOOK = r"""
(() => {
  if (window.top !== window || window.__mm) return;
  const mm = window.__mm = { timers: new Map(), fetching: 0, applied: null, heard: [], streams: 0 };
  const later = window.setTimeout, cancel = window.clearTimeout;
  window.setTimeout = function (fn, ms, ...args) {
    const delay = Math.max(0, Number(ms) || 0);
    let id;
    id = later.call(window, function (...a) {
      mm.timers.delete(id);
      return typeof fn === "function" ? fn.apply(this, a) : undefined;
    }, ms, ...args);
    mm.timers.set(id, [Date.now() + delay, delay]);
    return id;
  };
  window.clearTimeout = function (id) { mm.timers.delete(id); return cancel.call(window, id); };
  const frame = window.requestAnimationFrame, unframe = window.cancelAnimationFrame;
  window.requestAnimationFrame = function (fn) {
    let id;
    id = frame.call(window, (t) => { mm.timers.delete("f" + id); return fn(t); });
    mm.timers.set("f" + id, [Date.now() + 16, 16]);
    return id;
  };
  window.cancelAnimationFrame = function (id) { mm.timers.delete("f" + id); return unframe.call(window, id); };
  const fetchIt = window.fetch;
  window.fetch = function (...a) {
    mm.fetching++;
    return fetchIt.apply(this, a).finally(() => { mm.fetching--; });
  };
  const Stream = window.EventSource;
  if (Stream) {
    window.EventSource = class extends Stream {
      constructor(...a) { super(...a); mm.streams++; }
      addEventListener(type, fn, options) {
        const run = typeof fn === "function" ? fn : (e) => fn.handleEvent(e);
        return super.addEventListener(type, function (e) {
          try { return run.call(this, e); }
          finally {
            if (e.lastEventId) {
              mm.applied = e.lastEventId;
              mm.heard.push(e.lastEventId + " " + type);
            }
          }
        }, options);
      }
    };
  }
})();
"""

# Whether the keyboard is in a composer's body, where typed words are a person's own: the oracle holds them kept.
INTO_BODY = """() => !!document.activeElement?.matches('[data-mm="composer"] textarea[aria-label="message body"]')"""

PROBE = """(shortMs) => {
  const mm = window.__mm;
  if (!mm) return null;
  const now = Date.now();
  // A short animation or transition (a fade in, a slide) is a short delay too, and runs out at once: the clock doesn't
  // drive them, so they are finished here rather than waited for. One that loops, or lasts longer, is left.
  let finished = 0;
  for (const a of document.getAnimations()) {
    const t = a.effect && a.effect.getComputedTiming();
    if (a.playState === "running" && t && Number.isFinite(t.endTime) && t.endTime <= shortMs) {
      a.finish();
      finished++;
    }
  }
  // Each pending delay as [how long until it is due, how long it was set for]. A delay is short by what it was set for,
  // so a long one (a toast's life) stays long however near the short ones run the clock to it.
  return { pending: [...mm.timers.values()].map(([due, delay]) => [due - now, delay]), fetching: mm.fetching, applied: mm.applied,
           heard: mm.heard.length, streams: mm.streams, finished };
}"""

# What the page may say on its console without failing the test: Chromium refusing the clock's script in a mail
# body's frame, which is sandboxed without scripts (as tests/e2e/browser/conftest.py has it).
SANDBOX_REFUSAL = re.compile(r"Blocked script execution in '(about:srcdoc|about:blank|[^']*/api/message/(?:body|attachment/preview)\.html\?[^']*)' because the "
                             r"document's frame is sandboxed and the 'allow-scripts' permission is not set\.")
CSP_VIOLATION = "violates the following Content Security Policy directive"

# Keys as a scenario names them, as Playwright does.
KEYS = {"cmd": "Meta", "command": "Meta", "ctrl": "Control", "control": "Control", "alt": "Alt", "option": "Alt",
        "shift": "Shift", "esc": "Escape", "escape": "Escape", "enter": "Enter", "return": "Enter", "tab": "Tab",
        "space": " ", "backspace": "Backspace", "delete": "Delete", "up": "ArrowUp", "down": "ArrowDown",
        "left": "ArrowLeft", "right": "ArrowRight", "pageup": "PageUp", "pagedown": "PageDown", "home": "Home",
        "end": "End"}

# Regions a person names: by anchor (anchors.toml: data-mm on the page), or by role and label where those are enough.
REGIONS = {
    "list": ("anchor", "list"),
    "reader": ("anchor", "reader"),
    "composer": ("anchor", "composer"),
    "toast": ("anchor", "toast"),
    "float": ("anchor", "float"),
    "tray": ("anchor", "tray"),
    "rail": ("role", "complementary", "Accounts and folders"),
    "top bar": ("role", "banner", None),
    "brief": ("role", "region", "Today’s brief"),
    "dialog": ("role", "dialog", None),
}
# The page's roles for the roles a scenario names.
ROLES = {"row": "option"}


class Unsettled(AssertionError):
    pass


class Element:
    """Something on the page, found by role and label within a region. Acting on it goes through the client, so it is
    real input and settles."""

    def __init__(self, client, locator, description):
        self.client, self.locator, self.description = client, locator, description

    def __repr__(self):
        return f"<{self.description}>"

    @property
    def count(self):
        return self.locator.count()

    @property
    def exists(self):
        return self.count > 0

    @property
    def visible(self):
        """On screen: in the window's visible content, not hidden, not transparent."""
        if self.count != 1:
            return False
        return bool(self.locator.evaluate("""(el) => {
            if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
        }"""))

    @property
    def enabled(self):
        return self.locator.is_enabled()

    @property
    def focused(self):
        return bool(self.locator.evaluate("(el) => el === document.activeElement || el.contains(document.activeElement)"))

    @property
    def selected(self):
        return self.locator.evaluate(
            "(el) => el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-pressed') === 'true'")

    @property
    def text(self):
        return " ".join(self.locator.inner_text().split())

    @property
    def value(self):
        return self.locator.input_value()

    def click(self, count=1):
        self.client.click(self, count)

    def find(self, role, label=None, **where):
        """Something inside this one."""
        return self.client.find(role, label, within=self, **where)


class PageClient:
    """One page in its own browser context, on the world's server. It opens lazily, on its first action."""

    kind = "page"
    keys_reach_the_page = True            # with nothing focused, the page hears keys document-wide (the oracle's)

    def __init__(self, world, browser, *, viewport=(1440, 900), trace=None):
        self.world, self.browser, self.viewport, self.trace = world, browser, viewport, trace
        self.context = self.pw = None
        self.problems, self.blocked = [], []
        self._in_flight = {}
        self._requests, self._heard_from = [], 0
        world.clients.append(self)

    # ── opening ─────────────────────────────────────────────────────────

    @property
    def origin(self):
        return self.world.app.origin

    def _ensure_open(self):
        if self.pw is None:
            self.open("/")

    def open(self, path="/"):
        """The page at `path` (`/`, `/m/<account>/INBOX`…), as a person opening that link would."""
        with self._step(f"open {path}"):
            if self.pw is None:
                self._start()
            self.pw.goto(self.origin + path)

    def _start(self):
        width, height = self.viewport
        self.context = self.browser.new_context(viewport={"width": width, "height": height}, locale="en-US",
                                                timezone_id="UTC")
        self.context.route("**/*", self._fence)
        self.pw = self.context.new_page()
        self.pw.set_default_timeout(10_000)
        # The world's time, standing still (the module's docstring says why).
        at = self.world.clock.now
        self.pw.clock.install(time=at)
        self.pw.clock.pause_at(at)
        self.pw.add_init_script(HOOK)
        self.pw.on("pageerror", lambda e: self.problems.append(f"pageerror: {e}"))
        self.pw.on("console", self._console)
        self.pw.on("request", self._sent)
        self.pw.on("requestfinished", self._ended)
        self.pw.on("requestfailed", self._ended)

    def _fence(self, route):
        """The page reaches its own origin and nothing else: anything else is answered empty here, and listed."""
        url = route.request.url
        if url.startswith(self.origin + "/") or url.startswith(("data:", "blob:")):
            route.continue_()
        else:
            self.blocked.append(url)
            route.fulfill(status=200, body="")

    def _console(self, m):
        if m.type != "error" and CSP_VIOLATION not in m.text:
            return
        if SANDBOX_REFUSAL.match(m.text):
            return
        self.problems.append(f"console.{m.type}: {m.text}")

    def _path(self, url):
        return url[len(self.origin):] if url.startswith(self.origin) else url

    def _sent(self, request):
        path = self._path(request.url)
        if path.startswith(EVENTS):
            return
        self._in_flight[id(request)] = f"{request.method} {path.split('?')[0]}"

    def _ended(self, request):
        what = self._in_flight.pop(id(request), None)
        if what is not None:
            response = request.failure and "failed" or ""
            if not response:
                try:
                    got = request.response()
                    response = str(got.status) if got else ""
                except Exception:               # the page navigated away from it
                    response = "gone"
            self._requests.append(f"{what} {response}".strip())

    def close(self):
        if self.context is not None:
            self.context.close()
            self.context = self.pw = None
        if self in self.world.clients:
            self.world.clients.remove(self)

    # ── the client interface ────────────────────────────────────────────

    def press(self, keys, layout="us"):
        """Keys as a person presses them: "e", "cmd+shift+f", "pagedown"; several, apart: "g i"."""
        if layout != "us":
            raise NotImplementedError("the page has no keyboard layouts of its own: Chromium sends keys, not key codes")
        self._ensure_open()
        with self._step(f"press {keys}"):
            for chord in keys.split():
                self.pw.keyboard.press("+".join(KEYS.get(k.lower(), k) for k in chord.split("+")))

    def type(self, text):
        """Words typed where the keyboard is."""
        self._ensure_open()
        if self.trace is not None:
            self.trace.typed_into(text, self.pw.evaluate(INTO_BODY))
        with self._step(f"type {text!r}"):
            self.pw.keyboard.type(text)

    def click(self, target, count=1):
        self._ensure_open()
        with self._step(f"click {target.description}" + (f" ×{count}" if count > 1 else "")):
            target.locator.click(click_count=count)

    def drag(self, source, onto):
        self._ensure_open()
        with self._step(f"drag {source.description} onto {onto.description}"):
            source.locator.drag_to(onto.locator)

    def choose_menu(self, *path):
        raise NotImplementedError("the page has no menu bar: a scenario for the page uses the palette or a key")

    def find(self, role, label=None, within=None, exact=True, near=None, **where):
        """Something on the page by role and label, within a region (REGIONS, or an Element). A row is found by what it
        shows: `find("row", within="list", subject="Venue")`, or `sender=`. `near` picks the one in the region's part
        that says it: `find("button", "Cancel", within="tray", near="Fabric order")`."""
        self._ensure_open()
        scope, place = self._region(within)
        if near is not None:
            scope = scope.locator(":scope > *", has_text=near)
            place = f"{place}, by {near!r}"
        found = scope.get_by_role(ROLES.get(role, role), **({"name": label, "exact": exact} if label is not None else {}))
        for part, value in where.items():
            selector = {"subject": ".sj", "sender": ".from"}.get(part)
            if selector is None:
                raise TypeError(f"a {role} can't be found by {part}")
            found = found.filter(has=self.pw.locator(selector, has_text=re.compile("^" + re.escape(value) + "$")))
        said = f'{role}{f" {label!r}" if label is not None else ""}'
        said += "".join(f" {k}={v!r}" for k, v in where.items())
        return Element(self, found, said + (f" in {place}" if place else ""))

    def region(self, name):
        """A named region as an Element."""
        scope, place = self._region(name)
        return Element(self, scope, place)

    def _region(self, within):
        if within is None:
            return self.pw, ""
        if isinstance(within, Element):
            return within.locator, within.description
        how = REGIONS.get(within)
        if how is None:
            raise KeyError(f"no region {within!r}: name one of {', '.join(REGIONS)}")
        if how[0] == "anchor":
            return self.pw.locator(f'[data-mm="{how[1]}"]:visible').first, within
        _, role, label = how
        return (self.pw.get_by_role(role, name=label) if label else self.pw.get_by_role(role)).first, within

    def shown(self):
        self._ensure_open()
        return Shown.read(self.pw.evaluate(READ))

    def truth(self):
        """What the core holds at this mark, as the oracle reads it."""
        from . import oracle
        return oracle.read(self.world, self.shown() if self.showing() else None)

    def showing(self):
        """Whether the page is open and its script loaded: what `shown` reads."""
        return self.pw is not None and self._loaded()

    def picture(self, path=None):
        """A PNG of the page as it is now."""
        self._ensure_open()
        if path is None:
            fd, path = tempfile.mkstemp(prefix="mm-picture-", suffix=".png")
            os.close(fd)
        self.pw.screenshot(path=path)
        return path

    # ── settling ────────────────────────────────────────────────────────

    def settle(self, ceiling=SETTLE_S):
        """Wait until the page, the core and the fakes are quiet, running short delays at once (the module says what
        quiet means). Fails with what was still busy."""
        if self.pw is None:
            return 0.0
        started = time.monotonic()
        deadline, ran, quiet = started + ceiling, 0, 0
        busy = []
        while True:
            state = self.pw.evaluate(PROBE, SHORT_MS)
            busy = []
            if state is None:
                busy.append("the page hasn't loaded its script yet")
            else:
                short = [left for left, delay in state["pending"] if delay <= SHORT_MS]
                # While a request is out, the page's time stands: a frame loop waiting on it would otherwise run the
                # page's clock ahead by as long as the request took, and a toast's life with it.
                if short and not state["fetching"] and not self._in_flight:
                    if ran > RUN_AHEAD_MS:
                        raise Unsettled(f"the page keeps setting short delays: ran {ran} ms of them, still {sorted(short)[:5]}")
                    step = max(1, int(min(short)) + 1)
                    self.pw.clock.run_for(step)
                    ran += step
                    quiet = 0
                    continue
                if state["finished"]:
                    quiet = 0
                if state["fetching"]:
                    busy.append(f"{state['fetching']} fetch(es) in flight")
                if self._in_flight:
                    busy.append("requests in flight: " + ", ".join(sorted(set(self._in_flight.values()))))
                if not busy:
                    behind = self._behind(state)
                    if behind:
                        busy.append(behind)
            if busy:
                quiet = 0
            else:
                quiet += 1
                if quiet >= QUIET_PROBES:
                    self.world.app.settle()
                    # A job the app kicked runs now, as its lane would have: a pass of each, and settle again if the
                    # core said anything of it.
                    before = self.world.high_water()
                    self.world.app.work("kicked")
                    if self.world.high_water() == before:
                        break
                    quiet = 0
                    continue
            if time.monotonic() > deadline:
                raise Unsettled(f"not settled after {ceiling:.0f}s: " + "; ".join(busy))
            self.pw.wait_for_timeout(10 if busy else 15)
        took = (time.monotonic() - started) * 1000
        if self.trace is not None:
            self.trace.settled(took)
        return took

    def _behind(self, state):
        """Why the page hasn't applied the core's last event, or None when it has."""
        core = self.world.high_water()
        applied = state["applied"]
        if applied is None:
            return "the page's event stream hasn't opened" if state["streams"] else "the page opened no event stream"
        run, _, n = core.rpartition("-")
        page_run, _, page_n = applied.rpartition("-")
        if page_run != run:
            return f"the page's event stream is of another run of the app ({applied}; the core is at {core})"
        if int(page_n) < int(n):
            return f"the page has applied events up to {applied}; the core is at {core}"
        return None

    # ── the world tells it ──────────────────────────────────────────────

    def clock_moved(self, delta):
        """The world's clock moved on: the page's jumps as far, firing each timer that came due once, as a laptop's lid
        opened again would (Playwright's fast_forward)."""
        if self.pw is not None:
            self.pw.clock.fast_forward(int(delta.total_seconds() * 1000))

    def app_restarted(self):
        """A new process serves the page: open it again, where it was."""
        if self.pw is not None:
            self.pw.reload()

    # ── steps ───────────────────────────────────────────────────────────

    def _step(self, phrase):
        return _Acting(self, phrase)

    def begin_step(self):
        self._requests = []
        self._heard_from = self._heard_count()
        self._before = self.shown() if self.pw is not None and self._loaded() else None

    def end_step(self):
        out = {"requests": list(self._requests)}
        if self.pw is not None and self._loaded():
            out["events"] = self.pw.evaluate("(n) => window.__mm.heard.slice(n)", self._heard_from)
            out["shown"] = self.shown().diff(self._before)
        return out

    def _loaded(self):
        try:
            return bool(self.pw.evaluate("() => !!window.__mm"))
        except Exception:                         # mid-navigation
            return False

    def _heard_count(self):
        if self.pw is None or not self._loaded():
            return 0
        return self.pw.evaluate("() => window.__mm.heard.length")


class _Acting:
    """An action: a step of its own unless a screen's phrase holds it, settled once it's done."""

    def __init__(self, client, phrase):
        self.client, self.phrase = client, phrase

    def __enter__(self):
        trace = self.client.trace
        self._step = trace.step(self.phrase) if trace is not None else None
        if self._step is not None:
            self._step.__enter__()
        return self

    def __exit__(self, kind, error, tb):
        try:
            if kind is None:
                self.client.settle()
        finally:
            if self._step is not None:
                self._step.__exit__(kind, error, tb)
        return False


def launch(headed=False):
    """Playwright and a Chromium, for a run that isn't pytest's (`mm up page`)."""
    from playwright.sync_api import sync_playwright
    playwright = sync_playwright().start()
    return playwright, playwright.chromium.launch(headless=not headed)

