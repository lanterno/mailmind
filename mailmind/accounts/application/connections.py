"""What the settings page does to an account: try its login, trust its certificate again, change the file."""
import time
from collections.abc import Callable, Mapping
from typing import Any

from mailmind.accounts.application.directory import Directory
from mailmind.accounts.application.ports import ConnectionProbe
from mailmind.accounts.domain.account import Entry
from mailmind.accounts.domain import login_words, preset
from mailmind.accounts.domain.draft import DraftRefused, throwaway_account
from mailmind.kernel.errors import NotFound, Refused
from mailmind.kernel.parsed import is_object


PROBE_TIMEOUT = 10      # seconds, for each step of one: a short one gives quick word of a bad host


class _Outgoing(Exception):
    """The outgoing (SMTP) server's login didn't work, once the IMAP server's had: its cause is why."""


class Connections:
    def __init__(self, config: Directory, get: Callable[[str], Entry], accounts: Callable[[], list[Entry]],
                 probe: ConnectionProbe, quiet_error: Callable[[BaseException, str | None], str],
                 retry_sync: Callable[[Entry], dict[str, Any]], forget_status: Callable[[str | None], None],
                 forget_mail: Callable[[str], int] | None = None):
        self._config = config
        self._get = get
        self._accounts = accounts
        self._probe = probe
        self._quiet_error = quiet_error
        self._retry_sync = retry_sync
        self._forget_status = forget_status
        self._forget_mail = forget_mail         # what this Mac keeps of an account's mail, let go; None where none is kept

    def test(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Test a SAVED account ({email}), at its saved servers, or an UNSAVED draft (no disk write; what it may try
        is mailmind.accounts.domain.draft's to say): its IMAP server's login, then, once that has let it in, its outgoing
        (SMTP) server's. Errors are sanitized — never echoes the attempted password or server banner, and a Gmail account's
        refusal is said as Gmail's (`conn_error.quiet`), the outgoing server's after "SMTP ". A login that didn't work is an
        answer (`connect: false`): its class (`error`), and what a person is told of it and where they may go next (`says`,
        `link`, `login_words`); an account it doesn't know raises NotFound, and a draft it may not try Refused."""
        t0 = time.time()
        if body.get("draft") and not is_object(body["draft"]):
            raise Refused()
        kind = host = None      # the account's type and IMAP host, by which a refusal is put and its provider named
        try:
            # no IMAP gate: holding it here froze all mail ops for up to 60s on a mistyped/black-holed host
            if body.get("draft"):
                kind, host = body["draft"].get("type", "imap"), body["draft"].get("host")
                acct = throwaway_account(body["draft"])
                self._probe.draft(acct, timeout=PROBE_TIMEOUT)
            else:
                acct = self._get(body["email"])
                kind, host = acct["kind"], acct.get("host")
                self._probe.saved(acct, timeout=PROBE_TIMEOUT)
            try:
                self._probe.outgoing(acct, timeout=PROBE_TIMEOUT)
            except Exception as e:
                raise _Outgoing() from e
            return {"ok": True, "connect": True, "email": acct["email"],
                    "detail": "login ok", "ms": int((time.time() - t0) * 1000)}
        except KeyError:
            raise NotFound("account not found") from None
        except DraftRefused as no:
            raise Refused(str(no)) from no
        except Exception as e:
            draft: Entry = body.get("draft") or {}
            email = body.get("email") or draft.get("email")
            cause = e.__cause__ if isinstance(e, _Outgoing) else None
            if cause is not None:
                quiet = self._quiet_error(cause, None)
                error, said = "SMTP " + quiet, login_words.said_of(quiet, kind, host, email, outgoing=True)
            else:
                error = self._quiet_error(e, kind)
                said = login_words.said_of(error, kind, host, email)
            return {"ok": True, "connect": False, "email": email, "error": error, "says": said.says,
                    "link": preset.link_dict(said.link)}


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
        """Add, edit or delete an account, and with `delete_mail` what this Mac keeps of a deleted one's mail (`mail_deleted`,
        how many stored copies went). Raises NotFound for one the file doesn't hold, and Refused for any other action."""
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
                if body.get("delete_mail") and self._forget_mail is not None:     # its mail on the server is untouched
                    return {"ok": True, "removed": em, "mail_deleted": self._forget_mail(str(em))}
                return {"ok": True, "removed": em}
            raise NotFound("not found")
        raise Refused("unknown action")
