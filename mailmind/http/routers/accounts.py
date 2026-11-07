"""What the settings page asks of Accounts: the list, masked, and a change to it."""
from mailmind.http.routing import ANSWERED, Routes
from mailmind.kernel.errors import Refused
from mailmind.kernel.when import now_iso


def routes(accounts, mailbox):
    r = Routes()
    config = accounts.config

    @r.get("/api/accounts")
    def listed(req):
        return {"ok": True, "accounts": config.masked_rows(),
                "signature_default": config.signature_default(), "generated": now_iso()}

    @r.get("/api/account-health")
    def account_health(req):
        return accounts.health.answer()

    @r.matching("POST", lambda path: path.startswith("/api/accounts/"))
    def change(req):
        action = req.path.rsplit("/", 1)[-1]
        if action == "test":
            return accounts.connections.test(req.body)
        if action == "trust-cert":
            return accounts.connections.trust_cert(req.body)
        if action in ("add", "edit", "delete"):
            try:
                return accounts.connections.change(action, req.body)
            except ValueError as ve:
                raise Refused(str(ve)) from ve
            finally:
                mailbox.stats.invalidate()
        req.handler._send(404, b"not found", "text/plain")
        return ANSWERED

    return r
