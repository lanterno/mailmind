"""What the settings page asks of Accounts: the list, masked, and a change to it. Each action names its path, so one that isn't
an action goes on to the handler's `not found`, as it always answered."""
from mailmind.http import route
from mailmind.http.schemas.accounts import (
    AccountBody,
    CouldntLogIn,
    Listed,
    LoggedIn,
    NamesAnAccount,
    NotDone,
    Removed,
    Saved,
    Standing,
    Trusted,
    TryBody,
)
from mailmind.http.schemas.common import answers
from mailmind.kernel.errors import Refused
from mailmind.kernel.when import now_iso


def router(accounts, mailbox):
    r = route.router()
    config = accounts.config

    def change(action, asked):
        try:
            return accounts.connections.change(action, asked)
        except ValueError as ve:        # its text is one of the use case's own sentences, and holds no password
            raise Refused(str(ve)) from ve
        finally:
            mailbox.stats.invalidate()

    @r.get("/api/accounts", responses=answers(Listed))
    def listed():
        return {"ok": True, "accounts": config.masked_rows(),
                "signature_default": config.signature_default(), "generated": now_iso()}

    @r.get("/api/account-health", responses=answers(Standing))
    def account_health():
        return accounts.health.answer()

    @r.post("/api/accounts/test", responses=answers(LoggedIn | CouldntLogIn | NotDone))
    def test(body: TryBody):
        return accounts.connections.test(body.model_dump(exclude_unset=True))

    @r.post("/api/accounts/trust-cert", responses=answers(Trusted))
    def trust_cert(body: NamesAnAccount):
        return accounts.connections.trust_cert(body.model_dump(exclude_unset=True))

    @r.post("/api/accounts/add", responses=answers(Saved))
    def add(body: AccountBody):
        return change("add", body.model_dump(exclude_unset=True))

    @r.post("/api/accounts/edit", responses=answers(Saved | NotDone))
    def edit(body: AccountBody):
        return change("edit", body.model_dump(exclude_unset=True))

    @r.post("/api/accounts/delete", responses=answers(Removed | NotDone))
    def delete(body: NamesAnAccount):
        return change("delete", body.model_dump(exclude_unset=True))

    return r
