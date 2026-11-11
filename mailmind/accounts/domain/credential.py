"""The credential styles mailmind.config.json may name, and the one reader of them.

  { "keychain": "account:you@example.com",           a generic password in the login Keychain, under the service mailmind;
    "version": 2 }                                    how many MailMind has kept there, so a new one reads as a new credential
  { "plain": "the-password" }                       inline (the file is kept off git)
  "the-password"                                     the same, shorthand
  { "op": "op://Vault/Item/Field" }                 read from the 1Password CLI at runtime
  { "op": ["op://.../Field A", "op://.../Field B"] } refs tried in order
  { "env": "ENV_VAR_NAME" }                          read from an environment variable
  { "proton_bridge": true }                          the Proton Mail Bridge password

An entry naming two gets the first of plain, keychain, env, proton_bridge, op. Having the secret is
mailmind.accounts.adapters.secrets's; this only says where it is, and what MailMind names the Keychain items it keeps:
`account:<address>` for an account's password, `anthropic-api-key` for the Claude API key. No credential may name the
item the database's key is kept in, which would send that key to a mail server.

A password typed into Settings is `Typed` until it is saved: saving keeps it in the account's Keychain item and writes the
file the item's name (`keychain_credential`), and a draft's test tries it and keeps it nowhere. It isn't JSON, so no file
can hold one.
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Required, TypedDict, TypeIs

from mailmind.kernel.parsed import as_int, is_list, is_object


@dataclass(frozen=True)
class Typed:
    """A password typed into Settings, held in memory alone. It stays out of a repr."""
    password: str = field(repr=False)


# JSON as it parsed, so what a key holds is the file's word for it, or a password typed and not yet kept.
type Credential = str | dict[str, Any] | Typed | None
type Style = Literal["plain", "keychain", "typed", "op", "env", "proton_bridge", "unknown"]

API_KEY_ITEM = "anthropic-api-key"
DATABASE_KEY_ITEM = "database-key"      # mailmind.platform.db.key's, which no credential names


def account_item(email: str) -> str:
    """The Keychain item MailMind keeps `email`'s password in."""
    return f"account:{email}"


class CredentialMask(TypedDict, total=False):
    """A credential as the API may show it: its style and whether it's set, and only ever a pointer."""
    style: Required[str | None]
    set: Required[bool]
    ref: str
    var: str
    item: str


@dataclass(frozen=True)
class CredentialRef:
    """Where an account's secret is. `style` is None when there's no credential at all, and "unknown" when the file's
    says something no style reads. `named` is what the style's key holds: the Keychain item's name, the op reference or
    references, the variable's name, and for plain or a typed one the password itself, so it stays out of a repr.
    `version` is a Keychain credential's."""
    style: Style | None
    named: Any = field(default=None, repr=False)
    version: Any = None

    @classmethod
    def parse(cls, cred: object) -> "CredentialRef":
        if isinstance(cred, Typed):
            return cls("typed", cred.password)
        if isinstance(cred, str):
            return cls("plain", cred)
        if not is_object(cred) or not cred:
            return cls(None)
        if "plain" in cred:
            return cls("plain", cred["plain"])
        if "keychain" in cred:
            if cred["keychain"] == DATABASE_KEY_ITEM:
                return cls("unknown")
            return cls("keychain", cred["keychain"], cred.get("version"))
        if "env" in cred:
            return cls("env", cred["env"])
        if cred.get("proton_bridge"):
            return cls("proton_bridge", True)
        if "op" in cred:
            return cls("op", cred["op"])
        return cls("unknown")

    @property
    def op_refs(self) -> list[Any]:
        """The op references, in the order they're tried."""
        return self.named if is_list(self.named) else [self.named]

    def masked(self) -> CredentialMask:
        """Public, secret-FREE descriptor of a credential. NEVER returns a value,
        length, prefix, or hash — only the style and whether something is set."""
        if self.style == "keychain":                                            # the item's name is a pointer, safe
            return {"style": "keychain", "set": _text(self.named), "item": self.named if isinstance(self.named, str) else ""}
        if self.style == "op":
            return {"style": "op", "set": bool(self.named), "ref": self.named}   # ref is a pointer, safe
        if self.style == "env":
            return {"style": "env", "set": bool(self.named), "var": self.named}
        if self.style == "unknown":
            return {"style": "unknown", "set": False}
        return {"style": self.style, "set": bool(self.named)}


def _text(held: object) -> TypeIs[str]:
    return isinstance(held, str) and bool(held.strip())


def keychain_credential(item: str, before: object) -> dict[str, Any]:
    """The credential naming `item` once a secret is kept there. Its version is one past `before`'s when that named the
    same item, so the file says the secret changed: IDLE logs in afresh, a refused login's hold doesn't hold the new one,
    and a process that read the last one reads the Keychain again."""
    was = CredentialRef.parse(before)
    kept = as_int(was.version) if was.style == "keychain" and was.named == item else None
    return {"keychain": item, "version": max(kept or 0, 0) + 1}


def credential_from(p: object) -> dict[str, Any] | Typed | None:
    """What a request payload {style, value|ref|var} asks the file to hold: a stored credential, or a password typed for
    the Keychain (`Typed`), which the file never holds. Returns None to mean 'keep existing'. Raises ValueError on
    invalid input."""
    if not p:
        return None
    if not is_object(p):
        raise ValueError("a credential names its style")
    style = p.get("style")
    if style in ("keychain", "plain"):     # plain: the page's word for it until it says keychain
        v = p.get("value")
        if not (v and isinstance(v, str)):  # never blank out / store an empty password
            raise ValueError("password required")
        return Typed(v)
    if style == "op":
        ref = p.get("ref")
        if not (_text(ref) or (is_list(ref) and ref and all(_text(one) for one in ref))):
            raise ValueError("1Password reference required")
        return {"op": ref}
    if style == "env":
        var = p.get("var")
        if not _text(var):
            raise ValueError("environment variable name required")
        return {"env": var.strip()}
    if style == "proton_bridge":
        return {"proton_bridge": True}
    return None  # style unchanged / not provided -> keep existing
