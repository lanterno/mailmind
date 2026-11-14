"""What a person keeps in the login Keychain from Settings, beside each account's own password: the Claude API key, which the
config file names where the Assistant reads it, and the Proton Bridge's password, which every proton_bridge credential logs in
with. Each is kept with a POST and taken out with another, read through the Directory. No answer holds the secret, its length or
any part of it: each says only whether one is set. Keeping one writes nothing to a mail server, so a read-only process keeps it
too, as it keeps an account's typed password."""
from mailmind.http import route
from mailmind.http.schemas.common import NoBody, answers
from mailmind.http.schemas.keychain import KeyBody, Kept, PasswordBody
from mailmind.kernel.errors import Refused


def _in_words(keep):
    """`keep()`, a secret the Keychain can't hold refused in its own sentence, which holds no secret."""
    try:
        keep()
    except ValueError as ve:
        raise Refused(str(ve)) from None


def router(accounts):
    r = route.router()

    @r.get("/api/ai/key", responses=answers(Kept))
    def api_key():
        return {"ok": True, "set": accounts.config.api_key_set()}

    @r.post("/api/ai/key", responses=answers(Kept))
    def set_api_key(body: KeyBody):
        _in_words(lambda: accounts.config.set_api_key(body.key))
        return {"ok": True, "set": True}

    @r.post("/api/ai/key/clear", responses=answers(Kept))
    def clear_api_key(body: NoBody):
        accounts.config.clear_api_key()
        return {"ok": True, "set": False}

    @r.get("/api/accounts/bridge-password", responses=answers(Kept))
    def bridge_password():
        return {"ok": True, "set": accounts.config.bridge_password_set()}

    @r.post("/api/accounts/bridge-password", responses=answers(Kept))
    def set_bridge_password(body: PasswordBody):
        _in_words(lambda: accounts.config.set_bridge_password(body.password))
        return {"ok": True, "set": True}

    @r.post("/api/accounts/bridge-password/clear", responses=answers(Kept))
    def clear_bridge_password(body: NoBody):
        accounts.config.clear_bridge_password()
        return {"ok": True, "set": False}

    return r
