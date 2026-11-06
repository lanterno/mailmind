"""IMAP IDLE for MailMind (stdlib only): one worker per account watches its INBOX, and a
dispatcher coalesces a burst of changes into one InboxChanged on mailmind.platform.bus, which the
server answers with a sync of those accounts. The open tabs hear of the result through the live
event stream, mailmind.platform.sse.

A supervisor keeps the workers in line with the configuration: an account added gets a worker, one
removed or disabled loses its own, and one now reached another way (host, port, TLS, credential)
gets a fresh worker in place of the old. The account events (mailmind.accounts.domain.events) call
reconcile(), and a worker that finds its account changed under it leaves on its own.

Bounded (1 IDLE conn/account, capped accounts), reconnect-safe, daemon-thread clean exit.
"""
import threading

from mailmind.mailbox.application.ports import Accounts
from mailmind.mailbox.domain.events import InboxChanged
from mailmind.platform import bus

_ENDPOINT = ("kind", "host", "port", "starttls", "credential", "strip_spaces",   # how it's reached
             "imap_adapter")


class _Watch:
    """One account's worker: the account as configured when it started, and what stops it."""

    def __init__(self, acct, stopping):
        self.acct = acct
        self._stopping = stopping
        self.quit = False               # retired: its account is gone or now reached another way
        self.conn = None                # its connection, while it has one
        self.thread: threading.Thread | None = None

    def leaving(self):
        return self.quit or self._stopping()


def _same_endpoint(a, b):
    return all(a.get(k) == b.get(k) for k in _ENDPOINT)


class Realtime:
    IDLE_SECONDS = 24 * 60          # re-IDLE before servers drop (~29 min)
    MAX_IDLE_ACCOUNTS = 12
    _DEBOUNCE = 3                   # coalesce a burst of IDLE signals into one sync

    def __init__(self, accounts: Accounts, open_session):
        self._accounts = accounts
        self._open_session = open_session
        self._stop = threading.Event()
        self._conns = []                     # worker IMAP sessions, for force-close on stop()
        self._wake = threading.Condition()   # a worker backing off waits here; stop() and a retirement wake it
        self._watchers = {}                  # email -> _Watch: the workers start() and reconcile() run
        self._watch_lock = threading.Lock()
        self._running = False                # start() has run, and stop() hasn't
        self._dirty = set()
        self._dirty_lock = threading.Lock()
        self._kick = threading.Event()

    def _stopping(self):
        return self._stop.is_set()

    def _trigger_sync(self, email):
        with self._dirty_lock:
            self._dirty.add(email)
        self._kick.set()


    def _dispatch_loop(self):
        while not self._stop.is_set():
            self._kick.wait(timeout=self._DEBOUNCE)
            if self._stop.is_set():
                break
            self._kick.clear()
            with self._dirty_lock:
                todo = self._dirty.copy()
                self._dirty.clear()
            if todo:
                bus.publish(InboxChanged(frozenset(todo)))


    def _wanted(self, acct):
        """Is this account still configured, enabled and reached the same way? A configuration that
        can't be read keeps the worker watching."""
        try:
            return _same_endpoint(self._accounts.get(acct["email"]), acct)
        except KeyError:
            return False
        except Exception:
            return True


    def _rest(self, watch, seconds):
        with self._wake:
            self._wake.wait_for(watch.leaving, timeout=seconds)


    def _worker(self, acct, watch=None):
        watch = watch or _Watch(acct, self._stopping)
        email = acct["email"]
        backoff = 5
        while not watch.leaving():
            if not self._wanted(acct):
                print(f"[idle] {email}: removed, disabled or changed; this worker stops", flush=True)
                self.reconcile()                  # changed by hand: a worker for the new settings, if running
                return
            M = None
            try:
                M = self._open_session(acct)
                if "IDLE" not in M.capabilities:
                    print(f"[idle] {email}: server has no IDLE — falling back to periodic sync", flush=True)
                    try:
                        M.logout()
                    except Exception:
                        pass
                    return
                M.select("INBOX", readonly=False)
                self._conns.append(M)
                watch.conn = M               # set before leaving() is asked: _retire sets quit first
                backoff = 5
                while not watch.leaving():
                    changed = M.idle(self.IDLE_SECONDS)   # it leaves IDLE (sends DONE) before syncing
                    if watch.leaving():
                        break
                    if changed:
                        self._trigger_sync(email)          # sync runs on a SEPARATE short-lived conn
                    if not self._wanted(acct):
                        break                         # the loop's top says so and stops
            except Exception as e:
                if not watch.leaving():
                    print(f"[idle] {email}: {type(e).__name__}: {e}", flush=True)
                self._rest(watch, min(backoff, 300))
                backoff = min(backoff * 2, 300)
            finally:
                watch.conn = None
                try:
                    self._conns.remove(M)
                except ValueError:
                    pass
                if M:
                    try:
                        M.logout()               # closes the connection once the server answers
                    except Exception:
                        try:
                            M.shutdown()         # it didn't answer (stop() shut the socket down): close here
                        except Exception:
                            pass


    def _spawn(self, acct):
        watch = _Watch(acct, self._stopping)
        watch.thread = threading.Thread(target=self._worker, args=(acct, watch), name=f"idle:{acct['email']}",
                                        daemon=True)
        watch.thread.start()
        return watch


    def _retire(self, watch):
        """Stop a worker: wake it from a backoff, and from IDLE by shutting its socket down (see stop)."""
        with self._wake:
            watch.quit = True
            self._wake.notify_all()
        M = watch.conn
        if M is not None:
            M.interrupt()


    def reconcile(self, _event=None):
        """Bring the workers in line with the configuration: retire each whose account is gone, disabled
        or now reached another way, and start one for each account without one, up to
        self.MAX_IDLE_ACCOUNTS. Each account event calls it; before start() and after stop() it does nothing.
        Returns how many accounts are configured."""
        with self._watch_lock:
            if not self._running or self._stop.is_set():
                return 0
            try:
                wanted = {a["email"]: a for a in self._accounts.accounts()}
            except Exception as e:
                print(f"[idle] can't read the accounts ({type(e).__name__}); watching as before", flush=True)
                return 0
            for email, watch in list(self._watchers.items()):
                if email not in wanted or not _same_endpoint(watch.acct, wanted[email]):
                    self._retire(watch)
                    del self._watchers[email]
            for email, acct in wanted.items():
                if email not in self._watchers and len(self._watchers) < self.MAX_IDLE_ACCOUNTS:
                    self._watchers[email] = self._spawn(acct)
            return len(wanted)


    def watching(self):
        """The accounts with a worker, each with whether it is still running (it ends on a server
        without IDLE, and the periodic sync covers that account)."""
        with self._watch_lock:
            return {email: watch.thread.is_alive() for email, watch in self._watchers.items()}


    def start(self):
        """Launch the sync dispatcher, then one IDLE worker per enabled account (capped)."""
        with self._watch_lock:
            self._running = True
        threading.Thread(target=self._dispatch_loop, daemon=True).start()
        configured = self.reconcile()
        if configured > self.MAX_IDLE_ACCOUNTS:
            print(f"[idle] {configured} accounts > cap {self.MAX_IDLE_ACCOUNTS}; "
                  "extras rely on the periodic sync", flush=True)
        print(f"[idle] watching {len(self.watching())} account(s) in real time", flush=True)


    def stop(self):
        self._stop.set()
        self._kick.set()
        with self._watch_lock:
            self._running = False
            self._watchers.clear()
        with self._wake:
            self._wake.notify_all()
        for M in list(self._conns):
            # Wake a worker blocked reading its connection by shutting the socket down. Never close it
            # from here: the worker may be about to wait on that descriptor, and once closed its number
            # can go to the next socket opened, which the worker would then wait on (and read from).
            # The worker closes its own connection on the way out.
            M.interrupt()
