"""What the settings page does to an account: try its login, trust its certificate again, change the file."""
import time
from collections.abc import Callable, Mapping
from typing import Any

from mailmind.accounts.application.directory import Directory
from mailmind.accounts.application.ports import ConnectionProbe
from mailmind.accounts.domain.account import Entry
from mailmind.accounts.domain.draft import DraftRefused, throwaway_account
from mailmind.kernel.errors import NotFound, Refused
from mailmind.kernel.parsed import is_object


PROBE_TIMEOUT = 10      # seconds, for each step of one: a short one gives quick word of a bad host


class Connections:
    def __init__(self, config: Directory, get: Callable[[str], Entry], accounts: Callable[[], list[Entry]],
                 probe: ConnectionProbe, quiet_error: Callable[[BaseException], str],
                 retry_sync: Callable[[Entry], dict[str, Any]], forget_status: Callable[[str | None], None]):
        self._config = config
        self._get = get
        self._accounts = accounts
        self._probe = probe
        self._quiet_error = quiet_error
        self._retry_sync = retry_sync
        self._forget_status = forget_status

    def test(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Test a SAVED account ({email}), at its saved server, or an UNSAVED draft (no disk write; what it may try
        is mailmind.accounts.domain.draft's to say). Errors are sanitized — never echoes the attempted password or
        server banner. A login that didn't work is an answer (`connect: false`); an account it doesn't know raises NotFound,
        and a draft it may not try Refused."""
        t0 = time.time()
        if body.get("draft") and not is_object(body["draft"]):
            raise Refused()
        try:
            # no IMAP gate: holding it here froze all mail ops for up to 60s on a mistyped/black-holed host
            if body.get("draft"):
                acct = throwaway_account(body["draft"])
                self._probe.draft(acct, timeout=PROBE_TIMEOUT)
            else:
                acct = self._get(body["email"])
                self._probe.saved(acct, timeout=PROBE_TIMEOUT)
            return {"ok": True, "connect": True, "email": acct["email"],
                    "detail": "login ok", "ms": int((time.time() - t0) * 1000)}
        except KeyError:
            raise NotFound("account not found") from None
        except DraftRefused as no:
            raise Refused(str(no)) from no
        except Exception as e:
            draft: Entry = body.get("draft") or {}
            return {"ok": True, "connect": False,
                    "email": body.get("email") or draft.get("email"),
                    "error": self._quiet_error(e)}


    def trust_cert(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Trust the account's servers again after its loopback certificate changed (Bridge reinstalled
        or reset): forget its pins, and sync it, which pins the certificate it meets now."""
        email = (body.get("email") or "").strip()
        if not self._config.forget_tls_pins(email):
            raise NotFound("not found")
        if email in {a["email"] for a in self._accounts()}:    # a disabled one waits until enabled
            return self._retry_sync({"account": email})
        return {"ok": True, "account": email}


    def change(self, action: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Add, edit or delete an account. Raises NotFound for one the file doesn't hold, and Refused for any other action."""
        if action == "add":
            return {"ok": True, "account": self._config.add_account(body)}
        if action == "edit":
            row = self._config.update_account(body.get("email"), body)
            if not row:
                raise NotFound("not found")
            return {"ok": True, "account": row}
        if action == "delete":
            em = body.get("email")
            if self._config.remove_account(em):
                self._forget_status(em)
                return {"ok": True, "removed": em}
            raise NotFound("not found")
        raise Refused("unknown action")
