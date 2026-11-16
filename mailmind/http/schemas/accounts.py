"""What the settings page asks of Accounts, and what it answers.

Accounts words its own refusals (`Directory`, `account.refuse_bad_endpoints` and `validate_new`, `credential_from`), and the page
shows them. A field one of those judges is `Any` here, since a schema's `bad request` would swallow the sentence.

A credential comes in as the page's {style, value|ref|var}, the one place a request holds a password, and goes out only as
`CredentialShown`. No answer below declares a field that could hold a secret, so under test one that held any is a failure.
"""
from typing import Any, Literal

from pydantic import Field

from mailmind.accounts.api import Preset
from mailmind.http.schemas.common import Answer, Flag, QueryArgs, RequestBody


class NamesAnAccount(RequestBody):
    email: str | None = None


class AccountDraft(NamesAnAccount):
    """An account not saved yet, as `draft.throwaway_account` reads one. What it can't read is a failed test, not a failed
    request, and it refuses a stored secret in words of its own, so what it judges stays loose."""
    type: Any = None                # gmail, imap or proton: `validate_new` says so
    host: Any = None                # `refuse_bad_endpoints` says "host must be a host name"
    port: Any = None                # `_port` says "port must be 1–65535"
    starttls: Flag | None = None
    strip_spaces: Flag | None = None
    smtp_host: Any = None           # as `host`: "SMTP host must be a host name"
    smtp_port: Any = None           # as `port`: "SMTP port must be 1–65535"
    smtp_starttls: Flag | None = None
    # Only ever a {style, ...} mapping that works, and `credential_from` words the rest ("a credential names its style", "password
    # required"). It may hold a password, so it stays out of a repr.
    credential: Any = Field(default=None, repr=False)


class TryBody(NamesAnAccount):
    """A saved account by its address, or else a draft. Nothing else here reaches a server: a saved account goes to its saved
    endpoint with its saved credential, whatever else the request names."""
    draft: AccountDraft | None = None


class AccountBody(AccountDraft):
    """An account to add, or what an edit changes (`account.apply_fields`). The page sends none of `strip_spaces` and
    `disabled`, which the use case still reads, and `trash` only as a preset names it."""
    alias: str | None = None
    signature: str | None = None
    trash: str | None = None
    disabled: Flag | None = None


class CredentialShown(Answer):
    """`CredentialRef.masked`: its style, whether it's set, and at most a pointer to it, a Keychain item's name, an op reference
    or a variable's name."""
    style: str | None
    set: bool
    item: str | None = None
    ref: str | list[str] | None = None
    var: str | None = None


class DefaultsApplied(Answer):
    """Which of an account's settings came from its type's defaults. The page doesn't read this."""
    host: bool
    port: bool
    starttls: bool
    trash: bool
    smtp_host: bool
    smtp_port: bool
    smtp_starttls: bool


class AccountRow(Answer):
    """`account.masked_row`. The page reads no `smtp_*` here."""
    email: str | None
    type: str
    alias: str | None
    host: str | None
    port: int
    starttls: bool
    trash: str
    smtp_host: str | None
    smtp_port: int
    smtp_starttls: bool
    disabled: bool
    defaults_applied: DefaultsApplied
    credential: CredentialShown
    signature: str


class Listed(Answer):
    ok: Literal[True]
    accounts: list[AccountRow]
    signature_default: str
    generated: str


class AccountStanding(Answer):
    """`AccountHealth.as_row`. Of an error, the class's name and its kind, never what it said."""
    account: str
    last_ok_ts: str | None
    last_error_ts: str | None
    last_error_kind: str | None
    last_error: str | None
    sync_count: int
    healthy: bool
    live: str


class Standing(Answer):
    ok: Literal[True]
    accounts: list[AccountStanding]
    generated: str


class LoggedIn(Answer):
    ok: Literal[True]
    connect: Literal[True]
    email: str
    detail: str
    ms: int


class CouldntLogIn(Answer):
    """The test ran, and the login didn't work. `error` is `conn_error.quiet`'s, never what the server said."""
    ok: Literal[True]
    connect: Literal[False]
    email: str | None
    error: str


class Saved(Answer):
    ok: Literal[True]
    account: AccountRow


class Removed(Answer):
    ok: Literal[True]
    removed: str


class Trusted(Answer):
    ok: Literal[True]
    account: str


class PresetArgs(QueryArgs):
    email: str | None = None


class Link(Answer):
    """A page a person is sent to (a provider's, where its app password is made), and the words of its link."""
    url: str
    words: str


class PresetSettings(Answer):
    """What an account of a preset is saved as, in `AccountBody`'s fields: its type, and a generic IMAP account's servers
    and Trash. A field its type gives is None."""
    type: str
    host: str | None
    port: int | None
    starttls: bool | None
    smtp_host: str | None
    smtp_port: int | None
    smtp_starttls: bool | None
    trash: str | None


class PresetFound(Answer):
    """`Presets.preset`: a provider's preset for an address at one of its own domains, `sure`, which the form chooses; for
    one whose domain's mail servers are a provider's, offered (`sure` false); none for any other. `domain` is the
    address's, in lower case, or None when it names none a lookup could ask about. `name` is the provider's; `settings`
    what its account is saved as, None where MailMind can't log in to it yet (`supported` false); `note` what the form
    says before the password, and `link` where that password is made."""
    ok: Literal[True]
    domain: str | None
    preset: Preset | None
    sure: bool
    name: str | None
    supported: bool
    settings: PresetSettings | None
    note: str | None
    link: Link | None
