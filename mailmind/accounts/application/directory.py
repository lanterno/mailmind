"""MailMind account configuration.

Accounts live in `mailmind.config.json` (NOT git-tracked). Copy
`mailmind.config.example.json` to that name and fill it in. Each account:

  { "email": "...", "type": "gmail|imap|proton",
    "host": "...", "port": 993, "starttls": false, "trash": "Trash",
    "credential": { ... one of the styles below ... } }

An optional top-level "anthropic_api_key" holds the Claude API key, in any of the same styles
(proton_bridge aside). It wins over ANTHROPIC_API_KEY in the environment.

Credential styles (so this works for anyone, with or without 1Password): plain, op, env and
proton_bridge, which mailmind.accounts.adapters.secrets lists and resolves.

Type defaults (override per account as needed):
  gmail  -> imap.gmail.com:993 TLS, trash "[Gmail]/Trash", app-password spaces stripped
  imap   -> :993 TLS,            trash "Trash",          password used verbatim
  proton -> 127.0.0.1:1143 STARTTLS (local Bridge), trash "Trash"
"""
import os
import threading
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from mailmind.accounts.application.ports import ConfigStore, Secrets
from mailmind.accounts.domain import account, events
from mailmind.accounts.domain.account import Entry, MaskedRow
from mailmind.kernel.parsed import is_object
from mailmind.platform import bus as _bus
from mailmind.platform import settings


class _Bus(Protocol):
    def publish(self, event: object) -> None: ...


bus: _Bus = _bus    # mailmind.platform.bus's own publish leaves its event unannotated, until the platform is typed too


class Directory:
    TYPE_DEFAULTS = account.TYPE_DEFAULTS

    def __init__(self, file: ConfigStore, secrets: Secrets, ai_key: Callable[[], str | None]):
        self._file, self._secrets, self._ai_key = file, secrets, ai_key
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

    def signature_default(self) -> str:
        """The global default signature (used when an account has no per-account one)."""
        return account.clean_signature(self._raw().get("signature_default") or "")

    def set_signature_default(self, text: str) -> None:
        with self._write_lock:
            raw = self._raw()
            sg = account.clean_signature(text)
            if sg:
                raw["signature_default"] = sg
            else:
                raw.pop("signature_default", None)
            self.save(raw)

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
            cred = a.get("credential")
            if isinstance(cred, dict) and "op" in cred:
                try:
                    self.resolve_credential(a)
                    ok += 1
                except Exception as e:
                    print(f"  ! preload failed for {a['email']}: {e}")
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

    def masked_rows(self) -> list[MaskedRow]:
        return [account.masked_row(e) for e in self._raw().get("accounts", [])]

    def save(self, raw: Entry) -> None:
        """Atomically persist the whole config. Validates, fsyncs, chmod 600 before
        an atomic rename so a crash can never leave a partial/world-readable file."""
        self._file.write(raw)

    def add_account(self, p: Mapping[str, Any]) -> MaskedRow:
        with self._write_lock:
            raw = self._raw()
            raw.setdefault("accounts", [])
            email = (p.get("email") or "").strip()
            if account.find(raw, email):
                raise ValueError(f"{email} already exists")
            entry = {"email": email, "type": p.get("type", "imap")}
            account.apply_fields(entry, p)
            cred = account.credential_from(p.get("credential"))
            if cred is None:
                raise ValueError("a credential is required")
            entry["credential"] = cred
            account.validate_new(entry)
            raw["accounts"].append(entry)
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
            account.apply_fields(entry, p)
            if "credential" in p:
                new = account.credential_from(p["credential"])
                if new is not None:          # only replace when a real value/ref/var was given
                    entry["credential"] = new
                    self._secrets.evict(old_cred)
            account.validate_new(entry)
            self.save(raw)
        bus.publish(events.AccountChanged(entry["email"]))
        return account.masked_row(entry)

    def remove_account(self, email: str | None) -> bool:
        with self._write_lock:
            raw = self._raw()
            entry = account.find(raw, email)
            if entry is None:
                return False
            self._secrets.evict(entry.get("credential"))
            raw["accounts"] = [e for e in raw["accounts"] if e is not entry]
            self.save(raw)
        bus.publish(events.AccountRemoved(entry["email"]))
        return True

    def materialize(self, only: str | None = None) -> None:
        """Rewrite op/env/proton_bridge credentials as inline {plain:...} in
        mailmind.config.json, making it fully self-contained (no 1Password needed).
        The file is git-ignored. Pass an email to convert just one account."""
        raw = self._raw()
        changed = 0
        for entry in raw.get("accounts", []):
            if only and entry.get("email") != only:
                continue
            cred = entry.get("credential")
            if isinstance(cred, dict) and "plain" in cred or isinstance(cred, str):
                continue  # already inline
            try:
                pw = self.resolve_credential({"email": entry.get("email"), "credential": cred,
                                         "strip_spaces": account.strip_for(entry)})
            except Exception as e:
                print(f"  ! skip {entry.get('email')}: {e}")
                continue
            entry["credential"] = {"plain": pw}
            changed += 1
            print(f"  materialized {entry.get('email')}")
        if changed:
            self.save(raw)
        print(f"done — {changed} account(s) now self-contained in {os.path.basename(settings.current().config)}")

    def check(self) -> None:
        """Resolve every account's credential and report ok/fail WITHOUT printing secrets."""
        for a in self.accounts():
            try:
                v = self.resolve_credential(a)
                print(f"  ok    {a['email']:42} ({len(v)} chars)")
            except Exception as e:
                print(f"  FAIL  {a['email']:42} {e}")
        if self._raw().get("anthropic_api_key"):
            try:
                print(f"  ok    {'anthropic_api_key':42} ({len(self._ai_key() or '')} chars)")
            except Exception as e:
                print(f"  FAIL  {'anthropic_api_key':42} {e}")
