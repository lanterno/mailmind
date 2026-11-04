"""IMAP IDLE for MailMind (stdlib only): one worker per account watches its INBOX, and a
dispatcher coalesces a burst of changes into one InboxChanged on mailmind.platform.bus, which the
server answers with a sync of those accounts. The open tabs hear of the result through the live
event stream, mailmind.platform.sse.

Bounded (1 IDLE conn/account, capped accounts), reconnect-safe, daemon-thread clean exit.
"""
import socket
import threading
from dataclasses import dataclass

import accounts
from mailmind.platform import bus

IDLE_SECONDS = 24 * 60          # re-IDLE before servers drop (~29 min)
MAX_IDLE_ACCOUNTS = 12
_DEBOUNCE = 3                   # coalesce a burst of IDLE signals into one sync

_stop = threading.Event()
_conns = []                     # worker IMAP conns, for force-close on stop()
_dirty = set()
_dirty_lock = threading.Lock()
_kick = threading.Event()


@dataclass(frozen=True)
class InboxChanged:
    """IDLE saw these accounts' INBOX change, a burst of signals coalesced into one (published on
    mailmind.platform.bus). Whoever hears it syncs them."""
    accounts: frozenset[str]


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
        if todo:
            bus.publish(InboxChanged(frozenset(todo)))


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
            if M:
                try:
                    M.logout()               # closes the connection once the server answers
                except Exception:
                    try:
                        M.shutdown()         # it didn't answer (stop() shut the socket down): close here
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
    for M in list(_conns):
        # Wake a worker blocked reading its connection by shutting the socket down. Never close it
        # from here: the worker may be about to wait on that descriptor, and once closed its number
        # can go to the next socket opened, which the worker would then wait on (and read from).
        # The worker closes its own connection on the way out.
        try:
            M.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
