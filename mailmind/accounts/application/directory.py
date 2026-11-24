"""MailMind account configuration.

Accounts live in `mailmind.config.json` (NOT git-tracked). Copy
`mailmind.config.example.json` to that name and fill it in. Each account:

  { "email": "...", "type": "gmail|imap|proton",
    "host": "...", "port": 993, "starttls": false, "trash": "Trash",
    "credential": { ... one of the styles below ... } }

An optional top-level "anthropic_api_key" holds the Claude API key, in any of the same styles
(proton_bridge aside). It wins over ANTHROPIC_API_KEY in the environment. "openai_api_key", "gemini_api_key" and
"local_api_key" and "compatible_api_key" hold the other AI providers' keys the same way (#49). An optional top-level
"preferences" block holds what the Settings page sets (mailmind.accounts.domain.preferences);
with none, each preference is its default.

Credential styles (so this works for anyone, with or without 1Password): keychain, op, env and
proton_bridge, which mailmind.accounts.domain.credential lists and mailmind.accounts.adapters.secrets
resolves. The file never holds a secret itself: one that does is refused (`refuse_plaintext`), and
`set_password` or `set_api_key` keeps it in the Keychain instead. A password typed into Settings goes to the login
Keychain, in the account's own item (`account:<address>`), before the file is written, and the file names the item. A
Gmail App Password typed as Google shows it, in four groups of four letters, is kept without its spaces
(`account.as_kept`). A new one replaces what the item held, and removing the account takes its item out. A Keychain
that won't is `Unreachable`, and the file is left as it was. An AI provider's key, which the file names, and the Proton
Bridge's password, which it doesn't, are kept and taken out alike (`set_api_key`, `clear_api_key`,
`set_bridge_password`, `clear_bridge_password`).

Type defaults (override per account as needed):
  gmail  -> imap.gmail.com:993 TLS, trash "[Gmail]/Trash", app-password spaces stripped
  imap   -> :993 TLS,            trash "Trash",          password used verbatim
  proton -> 127.0.0.1:1143 STARTTLS (local Bridge), trash "Trash"
"""
import threading
from collections.abc import Callable, Mapping
from typing import Any

from mailmind.accounts.application.ports import AccountRepository, SecretStore
from mailmind.accounts.domain import account, events, preferences
from mailmind.accounts.domain.account import Entry, MaskedRow
from mailmind.accounts.domain.credential import (BRIDGE_ITEM, CredentialRef, PlaintextSecret, Typed, account_item,
                                                 api_key_item, api_key_text, api_key_who, credential_from, keychain_credential,
                                                 plaintext)
from mailmind.kernel.ai_provider import AI_PROVIDERS, AiProvider
from mailmind.kernel.errors import Unreachable
from mailmind.kernel.parsed import is_object
from mailmind.platform import bus
from mailmind.platform.keychain import KeychainProblem
from mailmind.platform.logging import get_logger

log = get_logger("accounts")


class Directory:
    def __init__(self, file: AccountRepository, secrets: SecretStore, ai_key: Callable[[AiProvider], str | None],
                 key_in_environment: Callable[[], bool] = lambda: False):
        self._file, self._secrets, self._ai_key = file, secrets, ai_key
        self._key_in_environment = key_in_environment
        self._write_lock = threading.Lock()

    def _raw(self) -> Entry:
        """The file's contents, a copy of the caller's own; parsed again only when the file changed."""
        return self._file.read()

    def accounts(self) -> list[Entry]:
        """Return normalized account dicts from the config (disabled ones skipped)."""
        return [n for n in map(account.normalized, self._raw().get("accounts", [])) if n]

    def alias_map(self) -> dict[str, str | None]:
        """email -> alias for ALL accounts (incl. disabled), so historical DB rows
        still render with their friendly name. Built from the raw file."""
        return {e["email"]: ((e.get("alias") or "").strip() or None)
                for e in self._raw().get("accounts", []) if e.get("email")}

    def colour_map(self) -> dict[str, int]:
        """email -> the place of its colour among the palette's (`account.colour_places`), for ALL accounts, as alias_map."""
        return account.colour_places([e for e in self._raw().get("accounts", []) if isinstance(e, dict)])

    def signature_default(self) -> str:
        """The global default signature (used when an account has no per-account one)."""
        return account.clean_signature(self._raw().get("signature_default") or "")

    def signature_map(self) -> dict[str, str]:
        """email -> effective signature (per-account if set, else the global default), for ALL
        raw accounts so the composer can insert/swap without an extra round trip."""
        default = self.signature_default()
        return {e["email"]: (account.clean_signature(e.get("signature")) or default)
                for e in self._raw().get("accounts", []) if e.get("email")}

    def webmail_map(self) -> dict[str, str | None]:
        """email -> webmail base URL (optional per-account 'webmail' key), used as a
        best-effort 'Open original' target for IMAP accounts (e.g. Nextcloud Mail)."""
        return {e["email"]: (e.get("webmail") or "").strip() or None
                for e in self._raw().get("accounts", []) if e.get("email")}

    def resolve_credential(self, acct: Mapping[str, Any]) -> str:
        return self._secrets.resolve(acct.get("credential"), who=acct["email"], strip=acct.get("strip_spaces", False))

    def preload(self) -> int:
        """Resolve every op-backed credential once (one Touch ID), so a long-running
        server never has to prompt again."""
        ok = 0
        for a in self.accounts():
            if CredentialRef.parse(a.get("credential")).style == "op":
                try:
                    self.resolve_credential(a)
                    ok += 1
                except Exception as e:
                    log.warning("%(account)s: a credential's preload failed", {"account": a["email"]}, exc_info=e)
        return ok

    def tls_pin(self, email: str, endpoint: str, seen: str) -> str | None:
        """The certificate pinned for the account `email` at `endpoint`: the one pinned before, or
        `seen`, pinned now on first use (mailmind.accounts.domain.tls_pin). None when no account is
        saved under `email`, as for a draft being tested: nothing is pinned for it."""
        entry = account.find(self._raw(), email)
        if entry is None:
            return None
        kept: dict[str, str] = entry.get("tls_pins") or {}
        pinned = kept.get(endpoint)
        if pinned:
            return pinned
        with self._write_lock:
            raw = self._raw()
            entry = account.find(raw, email)
            if entry is None:
                return None
            pins = entry.get("tls_pins")
            if not is_object(pins):
                pins = entry["tls_pins"] = {}
            if not pins.get(endpoint):
                pins[endpoint] = seen
                self.save(raw)
            return pins[endpoint]

    def forget_tls_pins(self, email: str | None) -> bool:
        """Trust the account's servers again: forget each certificate pinned for it, so the next
        connection pins the one it meets. Returns False when there is no such account."""
        with self._write_lock:
            raw = self._raw()
            entry = account.find(raw, email)
            if entry is None:
                return False
            if entry.pop("tls_pins", None) is not None:
                self.save(raw)
        bus.publish(events.AccountChanged(entry["email"]))
        return True

    def preferences(self) -> dict[str, Any]:
        """Every preference as it stands, AI mode on where the file doesn't say it and a key is there. Reading one writes
        nothing, so a file with no block keeps none."""
        return self._read(self._raw())

    def _read(self, raw: Entry) -> dict[str, Any]:
        return preferences.read(raw, preferences.ai_mode_default(raw, self._key_in_environment()))

    def preference(self, name: str) -> Any:
        """What the file says of one preference, or None where it says nothing that preference can be."""
        return preferences.said(self._raw(), name)

    def set_preferences(self, asked: Mapping[str, Any]) -> dict[str, Any]:
        """Keep what `asked` changes, and leave every other key in the file as it was. A value none can be, refused whole
        (`Refused`), writes nothing. The answer is each preference as it stands now."""
        return self._change_preferences(lambda raw: preferences.changes(asked, raw))

    def import_preferences(self, held: Mapping[str, Any]) -> dict[str, Any]:
        """What a browser held under its old keys, once, for what the file doesn't say yet. When there is nothing to take, nothing
        is written. The answer is each preference as it stands now."""
        return self._change_preferences(lambda raw: preferences.importable(held, raw))

    def _change_preferences(self, wanted_of: Callable[[Entry], Mapping[str, Any]]) -> dict[str, Any]:
        with self._write_lock:
            raw = self._raw()
            wanted = wanted_of(raw)
            if wanted:
                raw.setdefault("accounts", [])
                preferences.apply(raw, wanted)
                self.save(raw)
            return self._read(raw)

    def masked_rows(self) -> list[MaskedRow]:
        return [account.masked_row(e) for e in self._raw().get("accounts", [])]

    def save(self, raw: Entry) -> None:
        """Atomically persist the whole config. Validates, fsyncs, chmod 600 before
        an atomic rename so a crash can never leave a partial/world-readable file."""
        self._file.write(raw)

    def _kept(self, item: str, cred: dict[str, Any] | Typed, before: object) -> dict[str, Any]:
        """What the file holds of `cred`: a typed secret is kept in the Keychain as `item` first, and the file names the
        item. A secret that can't be kept (not printable ASCII, an address no item can be named for) raises ValueError in a
        sentence of the Keychain's, which holds no secret."""
        if not isinstance(cred, Typed):
            return cred
        try:
            self._secrets.keep(item, cred.password)
        except KeychainProblem:
            raise Unreachable("The Keychain didn't keep the password. Is the login keychain locked?") from None
        return keychain_credential(item, before)

    def _let_go(self, email: str) -> None:
        """Take the account's own Keychain item out, if it has one."""
        try:
            self._secrets.discard(account_item(email))
        except ValueError:
            pass                        # an address no item could be named for: there is none
        except KeychainProblem:
            raise Unreachable("The Keychain didn't let go of the password. Is the login keychain locked?") from None

    def add_account(self, p: Mapping[str, Any]) -> MaskedRow:
        with self._write_lock:
            raw = self._raw()
            raw.setdefault("accounts", [])
            email = (p.get("email") or "").strip()
            if account.find(raw, email):
                raise ValueError(f"{email} already exists")
            entry = {"email": email, "type": p.get("type", "imap")}
            account.apply_fields(entry, p)
            cred = credential_from(p.get("credential"))
            if cred is None:
                raise ValueError("a credential is required")
            account.refuse_bad_endpoints(entry, {})
            account.validate_new(entry)
            entry["credential"] = self._kept(account_item(entry["email"]), _typed_for(entry, cred), None)
            raw["accounts"].append(entry)
            account.keep_colours(raw["accounts"])          # the first colour no account wears, and each other's kept
            self.save(raw)
        bus.publish(events.AccountAdded(entry["email"]))
        return account.masked_row(entry)

    def update_account(self, email: str | None, p: Mapping[str, Any]) -> MaskedRow | None:
        with self._write_lock:
            raw = self._raw()
            entry = account.find(raw, email)
            if entry is None:
                return None
            old_cred = entry.get("credential")
            before = dict(entry)
            account.apply_fields(entry, p)
            new = credential_from(p["credential"]) if "credential" in p else None
            account.refuse_bad_endpoints(entry, before)
            account.validate_new(entry)
            if new is not None:                 # only replace when a real value/ref/var was given
                entry["credential"] = self._kept(account_item(entry["email"]), _typed_for(entry, new), old_cred)
                own = account_item(entry["email"])
                if _names(old_cred, own) and not _names(entry["credential"], own):
                    self._let_go(entry["email"])
                self._secrets.evict(old_cred)
            self.save(raw)
        bus.publish(events.AccountChanged(entry["email"]))
        return account.masked_row(entry)

    def remove_account(self, email: str | None) -> bool:
        with self._write_lock:
            raw = self._raw()
            entry = account.find(raw, email)
            if entry is None:
                return False
            self._let_go(entry["email"])
            self._secrets.evict(entry.get("credential"))
            account.keep_colours(raw["accounts"])          # as they are with it, so no other account's colour moves
            raw["accounts"] = [e for e in raw["accounts"] if e is not entry]
            self.save(raw)
        bus.publish(events.AccountRemoved(entry["email"]))
        return True

    def set_password(self, email: str | None, secret: str) -> str:
        """Keep `secret` as the account's password, in its own Keychain item, and point the file at the item, whatever its
        credential said before. The answer is the item's name. Raises KeyError for an account the file doesn't hold,
        ValueError for a secret the Keychain can't hold, and Unreachable when the Keychain won't keep it."""
        with self._write_lock:
            raw = self._raw()
            entry = account.find(raw, email)
            if entry is None:
                raise KeyError(email)
            old = entry.get("credential")
            entry["credential"] = self._kept(account_item(entry["email"]), _typed_for(entry, Typed(secret)), old)
            self._secrets.evict(old)
            self.save(raw)
        bus.publish(events.AccountChanged(entry["email"]))
        return entry["credential"]["keychain"]

    def set_api_key(self, secret: str, provider: AiProvider = "anthropic") -> str:
        """Keep `secret`, without the spaces and line breaks around it, as `provider`'s API key, in its Keychain item, and point
        the file's key for it (anthropic_api_key and the like) at the item, whatever it said before. The answer is the item's
        name. Raises as set_password does, and ValueError for a key with a space or a line break inside it."""
        who, item, key = api_key_who(provider), api_key_item(provider), api_key_text(secret)
        with self._write_lock:
            raw = self._raw()
            raw.setdefault("accounts", [])
            old = raw.get(who)
            raw[who] = self._kept(item, Typed(key), old)
            self._secrets.evict(old)
            self.save(raw)
        return item

    def api_key_set(self, provider: AiProvider = "anthropic") -> bool:
        """Whether the file names `provider`'s API key, wherever it keeps it."""
        return bool(self._raw().get(api_key_who(provider)))

    def api_keys_kept(self) -> list[AiProvider]:
        """Each provider whose API key the file names, wherever it keeps it, in the providers' order."""
        raw = self._raw()
        return [p for p in AI_PROVIDERS if raw.get(api_key_who(p))]

    def clear_api_key(self, provider: AiProvider = "anthropic") -> None:
        """Take `provider`'s API key's Keychain item out, and the file's key for it with it, whatever it named: for Claude,
        AI is off unless the environment gives a key. Raises Unreachable when the Keychain won't let go of the item, and the
        file is left as it was."""
        who = api_key_who(provider)
        with self._write_lock:
            raw = self._raw()
            self._take_out(api_key_item(provider), "key")
            if who in raw:
                self._secrets.evict(raw.pop(who))
                self.save(raw)

    def bridge_password_set(self) -> bool:
        """Whether the Keychain keeps the Proton Bridge's password: the environment or 1Password may give one all the
        same. Raises Unreachable when the Keychain can't be asked."""
        try:
            return self._secrets.holds(BRIDGE_ITEM)
        except KeychainProblem:
            raise Unreachable("The Keychain didn't answer. Is the login keychain locked?") from None

    def set_bridge_password(self, secret: str) -> None:
        """Keep `secret` as the Proton Bridge's password, in its Keychain item, which every proton_bridge credential logs in
        with: each such account has changed, so a login its server refused is let go and IDLE logs in afresh. Raises
        ValueError for a secret the Keychain can't hold, and Unreachable when it won't keep it."""
        try:
            self._secrets.keep(BRIDGE_ITEM, secret)
        except KeychainProblem:
            raise Unreachable("The Keychain didn't keep the password. Is the login keychain locked?") from None
        self._bridged_changed()

    def clear_bridge_password(self) -> None:
        """Take the Proton Bridge's password out of the Keychain, and say so of each account that logs in with it. Raises
        Unreachable when the Keychain won't."""
        self._take_out(BRIDGE_ITEM, "password")
        self._bridged_changed()

    def _take_out(self, item: str, what: str) -> None:
        try:
            self._secrets.discard(item)
        except KeychainProblem:
            raise Unreachable(f"The Keychain didn't let go of the {what}. Is the login keychain locked?") from None

    def _bridged_changed(self) -> None:
        for a in self.accounts():
            if CredentialRef.parse(a.get("credential")).style == "proton_bridge":
                bus.publish(events.AccountChanged(a["email"]))

    def refuse_plaintext(self) -> None:
        """Raise PlaintextSecret, naming each, when the file holds a secret itself."""
        held = plaintext(self._raw())
        if held:
            raise PlaintextSecret([who for who, *_ in held])

    def materialize(self, only: str | None = None) -> None:
        """Keep each account's secret that lives elsewhere (1Password, a variable, the Bridge password) in
        its own Keychain item, and point mailmind.config.json at it, so none of those is needed again.
        The file is git-ignored. Pass an email to convert just one account."""
        raw = self._raw()
        changed = 0
        for entry in raw.get("accounts", []):
            if only and entry.get("email") != only:
                continue
            cred = entry.get("credential")
            if CredentialRef.parse(cred).style == "keychain":
                continue  # kept there already
            try:
                pw = self.resolve_credential({"email": entry.get("email"), "credential": cred,
                                         "strip_spaces": account.strip_for(entry)})
                entry["credential"] = self._kept(account_item(entry["email"]), Typed(pw), cred)
            except Exception as e:
                print(f"  ! skip {entry.get('email')}: {e}")
                continue
            changed += 1
            print(f"  materialized {entry.get('email')}")
        if changed:
            self.save(raw)
        print(f"done — {changed} account(s) now kept in the Keychain")

    def check(self) -> None:
        """Resolve every account's credential and report ok/fail WITHOUT printing secrets."""
        for a in self.accounts():
            try:
                v = self.resolve_credential(a)
                print(f"  ok    {a['email']:42} ({len(v)} chars)")
            except Exception as e:
                print(f"  FAIL  {a['email']:42} {e}")
        for provider in AI_PROVIDERS:
            who = api_key_who(provider)
            if self._raw().get(who):
                try:
                    print(f"  ok    {who:42} ({len(self._ai_key(provider) or '')} chars)")
                except Exception as e:
                    print(f"  FAIL  {who:42} {e}")


def _typed_for(entry: Entry, cred: dict[str, Any] | Typed) -> dict[str, Any] | Typed:
    """A password typed for `entry` as it is kept: a Gmail App Password without the spaces Google shows it with
    (`account.as_kept`). Where a stored credential points stays as it is."""
    if isinstance(cred, Typed):
        return Typed(account.as_kept(cred.password, account.strip_for(entry)))
    return cred


def _names(cred: object, item: str) -> bool:
    ref = CredentialRef.parse(cred)
    return ref.style == "keychain" and ref.named == item
