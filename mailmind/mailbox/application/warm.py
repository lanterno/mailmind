"""Optional background multi-folder warming. Default OFF: with MAILMIND_WARM_FOLDERS unset
nothing runs, so search/threading stay INBOX-only exactly as before.

When enabled per-account, the always-on sync tick also pulls a per-account ROLE SET — Drafts,
Archive/All-Mail, Sent — so search and conversation threading cover more than just INBOX. It
REUSES ingest.sync_folder(), so it inherits every invariant for free: UIDVALIDITY check,
read-only SELECT, BODY.PEEK / headers-only for non-INBOX (no bandwidth blowup), watermark-only
incremental pulls, mute/follow-up resolution.

Politeness/safety:
  • Staggered: Drafts every tick, Archive every 2nd, Sent every 3rd (Sent is the heavy one).
  • First-touch bounded: a brand-new warm of a huge folder pulls only the most recent
    WARM_RECENT UIDs (recent= cap in sync_folder), so it never drags thousands in one pass.
  • Runs INSIDE sync_guarded's single-flight, AFTER the INBOX sync, so it never delays new mail.
  • A 0.5s pause between folders; each folder opens its own fresh connection (never the pool).

Enable:  MAILMIND_WARM_FOLDERS=1            → all accounts (Bridge included only if explicitly listed)
         MAILMIND_WARM_FOLDERS=a@x,b@y      → only those accounts
"""
import os
import threading
import time

from mailmind.mailbox.application import session as mail_session
from mailmind.mailbox.application.ports import Accounts

WARM_RECENT = 1500     # cap a first-touch warm to the most recent N messages per folder
_ROLES = {                          # what to warm -> the folder roles that may play it, in order
    "drafts": ("drafts",),
    "archive": ("all", "archive"),
    "sent": ("sent",),
}
_STAGGER = {"drafts": 1, "archive": 2, "sent": 3}   # warm role every Nth tick


def _enabled(acct):
    v = os.environ.get("MAILMIND_WARM_FOLDERS", "").strip()
    if not v:
        return False
    if v.lower() in ("1", "all", "on", "true", "yes"):
        return True
    return acct["email"] in {x.strip() for x in v.split(",") if x.strip()}


class Warming:
    def __init__(self, accounts: Accounts, open_session, sync_folder):
        self._accounts = accounts
        self._open_session = open_session
        self._sync_folder = sync_folder
        self._resolved = {}         # email -> {role: folder_name|None}  (folder names are stable; resolve once)
        self._lock = threading.Lock()
        self._tick = 0

    def _roles_for(self, acct):
        """Resolve (and cache) this account's Drafts/Archive/Sent folder names in one LIST."""
        with self._lock:
            if acct["email"] in self._resolved:
                return self._resolved[acct["email"]]
        out = {}
        try:
            s = self._open_session(acct)
            try:
                for role, hints in _ROLES.items():
                    try:
                        out[role] = mail_session.find_role(s, *hints)
                    except Exception:
                        out[role] = None
            finally:
                try:
                    s.logout()
                except Exception:
                    pass
        except Exception as e:
            print(f"[warm] resolve {acct['email']}: {type(e).__name__}", flush=True)
            return {}                       # don't cache a failed resolve — retry next tick
        with self._lock:
            self._resolved[acct["email"]] = out
        return out


    def warm_tick(self, emails=None):
        """One warming pass over the enabled accounts' due role folders. Called from sync_guarded
        after the INBOX sync. No-op unless MAILMIND_WARM_FOLDERS enables the account."""
        if not os.environ.get("MAILMIND_WARM_FOLDERS", "").strip():
            return
        self._tick += 1
        for a in self._accounts.accounts():
            if emails is not None and a["email"] not in emails:
                continue
            if not _enabled(a):
                continue
            roles = self._roles_for(a)
            for role, every in _STAGGER.items():
                if self._tick % every != 0:
                    continue
                # Gmail "All Mail" is a SUPERSET of INBOX (every message is also there) — warming it
                # would duplicate INBOX in search/threading. Skip Archive for Gmail; Sent/Drafts are
                # distinct. Non-Gmail Archive is a separate store, so warm it normally.
                if role == "archive" and self._accounts.profile(a).archive_is_label_removal:
                    continue
                folder = roles.get(role)
                if not folder or folder.upper() == "INBOX":
                    continue
                try:
                    r = self._sync_folder(a, folder, full=False, recent=WARM_RECENT)
                    if r and (r[0] or 0):
                        print(f"[warm] {a['email']} {folder}: +{r[0]}", flush=True)
                except Exception as e:
                    print(f"[warm] {a['email']} {folder}: {type(e).__name__}: {e}", flush=True)
                time.sleep(0.5)
