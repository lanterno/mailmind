"""What the settings page does to an account: try its login, trust its certificate again, change the file."""
import time


class Connections:
    def __init__(self, config, get, accounts, open_session, quiet_error, retry_sync, forget_status):
        self._config = config
        self._get = get
        self._accounts = accounts
        self._open_session = open_session
        self._quiet_error = quiet_error
        self._retry_sync = retry_sync
        self._forget_status = forget_status

    def test(self, body):
        """Test a SAVED account ({email}) or an UNSAVED draft (no disk write).
        Errors are sanitized — never echoes the attempted password or server banner.

        A saved account's credential goes only to its saved server. A draft may only try a password
        typed with it, against the host typed with it: a draft naming a stored secret (an op
        reference, an environment variable, the Bridge password) would send that secret to whatever
        host the request names, so it is refused until the account is saved."""
        t0 = time.time()
        try:
            if body.get("draft"):
                d = body["draft"]
                ty = d.get("type", "imap")
                td = self._config.TYPE_DEFAULTS.get(ty, self._config.TYPE_DEFAULTS["imap"])
                cs = (d.get("credential") or {})
                acct = {"email": d.get("email"), "kind": ty,
                        "host": d.get("host") or td["host"], "port": int(d.get("port") or td["port"]),
                        "starttls": bool(d.get("starttls", td["starttls"])),
                        "strip_spaces": bool(d.get("strip_spaces", td["strip_spaces"])),
                        "trash": td["trash"], "credential": {"plain": cs.get("value")}}
                if not acct["host"]:
                    return {"ok": False, "error": "a host is required"}
                if cs.get("style") != "plain" or not cs.get("value"):
                    return {"ok": False, "error": "save the account to test a stored secret; "
                                                  "before then only a typed password is tried"}
            else:
                acct = self._get(body["email"])
            # stateless probe — shares no IMAP state, selects no folder, uses no pooled conn, so it needs no
            # IMAP gate (holding it here froze all mail ops for up to 60s on a mistyped/black-holed host);
            # a short timeout also gives the user fast feedback on a bad host.
            s = self._open_session(acct, timeout=15)
            try:
                s.logout()
            except Exception:
                pass
            return {"ok": True, "connect": True, "email": acct["email"],
                    "detail": "login ok", "ms": int((time.time() - t0) * 1000)}
        except KeyError:
            return {"ok": False, "error": "account not found"}
        except Exception as e:
            return {"ok": True, "connect": False,
                    "email": body.get("email") or (body.get("draft") or {}).get("email"),
                    "error": self._quiet_error(e)}


    def trust_cert(self, body):
        """Trust the account's servers again after its loopback certificate changed (Bridge reinstalled
        or reset): forget its pins, and sync it, which pins the certificate it meets now."""
        email = (body.get("email") or "").strip()
        if not self._config.forget_tls_pins(email):
            return {"ok": False, "error": "not found"}, 404
        if email in {a["email"] for a in self._accounts()}:    # a disabled one waits until enabled
            return self._retry_sync({"account": email})
        return {"ok": True, "account": email}, 200


    def change(self, action, body):
        if action == "add":
            return {"ok": True, "account": self._config.add_account(body)}
        if action == "edit":
            row = self._config.update_account(body.get("email"), body)
            return {"ok": True, "account": row} if row else {"ok": False, "error": "not found"}
        if action == "delete":
            em = body.get("email")
            if self._config.remove_account(em):
                self._forget_status(em)
                return {"ok": True, "removed": em}
            return {"ok": False, "error": "not found"}
        return {"ok": False, "error": "unknown action"}
