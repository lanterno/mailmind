"""Real-time layer for MailMind (stdlib only):
  - per-account IMAP IDLE workers that detect INBOX changes and trigger a scoped sync,
  - a thread-safe pub/sub bus that feeds the hand-rolled SSE endpoint in server.py.

Events carry ONLY non-sensitive counters/ids (account/folder/count) — never subjects,
addresses, or bodies; the browser re-fetches through the existing authenticated routes.
Bounded (1 IDLE conn/account, capped subscribers), reconnect-safe, daemon-thread clean exit.
"""
import queue
import threading

import accounts

IDLE_SECONDS = 24 * 60          # re-IDLE before servers drop (~29 min)
MAX_IDLE_ACCOUNTS = 12
MAX_SUBS = 8
HEARTBEAT = 20                  # SSE keepalive seconds
_DEBOUNCE = 3                   # coalesce a burst of IDLE signals into one sync

_subs = []
_subs_lock = threading.Lock()
_stop = threading.Event()
_conns = []                     # worker IMAP conns, for force-close on stop()
_sync_handler = None            # set by server.configure(): fn(set_of_emails) -> syncs them
_dirty = set()
_dirty_lock = threading.Lock()
_kick = threading.Event()


def configure(sync_handler):
    global _sync_handler
    _sync_handler = sync_handler


# ------------------------------------------------------------------- pub/sub bus
def subscribe():
    with _subs_lock:
        if len(_subs) >= MAX_SUBS:
            return None
        q = queue.Queue(maxsize=64)
        _subs.append(q)
        return q


def unsubscribe(q):
    with _subs_lock:
        try:
            _subs.remove(q)
        except ValueError:
            pass


def publish(event, data):
    """Fan an (event, data) out to all subscribers; drop on a slow/full client queue."""
    with _subs_lock:
        targets = list(_subs)
    for q in targets:
        try:
            q.put_nowait((event, data))
        except queue.Full:
            pass


# ------------------------------------------------------------- IDLE sync dispatch
def _trigger_sync(email):
    with _dirty_lock:
        _dirty.add(email)
    _kick.set()


def _dispatch_loop():
    while not _stop.is_set():
        _kick.wait(timeout=_DEBOUNCE)
        if _stop.is_set():
            break
        _kick.clear()
        with _dirty_lock:
            todo = _dirty.copy()
            _dirty.clear()
        if not todo or not _sync_handler:
            continue
        try:
            _sync_handler(todo)
        except Exception as e:
            print(f"[idle] dispatch sync error: {type(e).__name__}: {e}", flush=True)


# --------------------------------------------------------------------- IDLE worker
def _worker(acct):
    email = acct["email"]
    backoff = 5
    while not _stop.is_set():
        M = None
        try:
            M = accounts.connect(acct)
            caps = getattr(M, "capabilities", ())
            if "IDLE" not in caps:
                print(f"[idle] {email}: server has no IDLE — falling back to periodic sync", flush=True)
                try:
                    M.logout()
                except Exception:
                    pass
                return
            M.select('"INBOX"')
            _conns.append(M)
            backoff = 5
            while not _stop.is_set():
                changed = False
                with M.idle(duration=IDLE_SECONDS) as idler:
                    for typ, data in idler:
                        if typ in ("EXISTS", "RECENT", "EXPUNGE"):
                            changed = True
                            break                # leave IDLE (sends DONE) before syncing
                if _stop.is_set():
                    break
                if changed:
                    _trigger_sync(email)          # sync runs on a SEPARATE short-lived conn
        except Exception as e:
            if not _stop.is_set():
                print(f"[idle] {email}: {type(e).__name__}: {e}", flush=True)
            _stop.wait(min(backoff, 300))
            backoff = min(backoff * 2, 300)
        finally:
            try:
                _conns.remove(M)
            except ValueError:
                pass
            try:
                if M:
                    M.logout()
            except Exception:
                pass


def start():
    """Launch one IDLE worker per enabled account (capped) + the sync dispatcher."""
    accts = accounts.accounts()[:MAX_IDLE_ACCOUNTS]
    if len(accounts.accounts()) > MAX_IDLE_ACCOUNTS:
        print(f"[idle] {len(accounts.accounts())} accounts > cap {MAX_IDLE_ACCOUNTS}; "
              "extras rely on the periodic sync", flush=True)
    threading.Thread(target=_dispatch_loop, daemon=True).start()
    for a in accts:
        threading.Thread(target=_worker, args=(a,), daemon=True).start()
    print(f"[idle] watching {len(accts)} account(s) in real time", flush=True)


def stop():
    _stop.set()
    _kick.set()
    for M in list(_conns):       # force-close so a blocked idler iterator unblocks
        try:
            M.shutdown()
        except Exception:
            pass
