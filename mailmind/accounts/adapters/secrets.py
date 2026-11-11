"""Having an account's password, from wherever its credential says (mailmind.accounts.domain.credential).

A Keychain item is read through mailmind.platform.keychain, and an op read through the 1Password CLI. Each is cached
for the process's life, and only when it succeeds; `evict` drops a credential's cached reads when it is replaced or
removed, and `forget` drops them all, as a new process starts with none.

The Bridge password comes from the first of: the environment variable MAILMIND_PROTON_BRIDGE_PASS;
1Password's op://Private/<MAILMIND_PROTON_OP_ITEM>/password, when that variable names an item;
the file ~/.config/mailmind/proton_bridge_pass. It is cached once found.
"""
import pathlib
import subprocess
import time

from mailmind.accounts.domain.credential import CredentialRef
from mailmind.platform import environment, keychain

BRIDGE_FILE = pathlib.Path.home() / ".config" / "mailmind" / "proton_bridge_pass"

_op_cache = {}
_keychain_cache = {}
_bridge = {}


def _op_read(ref, strip):
    if ref in _op_cache:
        return _op_cache[ref]
    last = ""
    for _ in range(3):
        r = subprocess.run(["op", "read", ref], capture_output=True, text=True)
        if r.returncode == 0:
            pw = r.stdout.strip()
            if strip:
                pw = pw.replace(" ", "")
            _op_cache[ref] = pw
            return pw
        last = r.stderr.strip()
        if "timeout" in last.lower() or "authorization" in last.lower():
            time.sleep(3)
            continue
        break
    raise RuntimeError(f"op read failed for {ref}: {last}")


def _keychain_read(item, who):
    if item in _keychain_cache:
        return _keychain_cache[item]
    secret = keychain.find(item)
    if secret is None:
        raise RuntimeError(f"the Keychain holds no item {item} for {who}")
    _keychain_cache[item] = secret
    return secret


def resolve(cred, *, who, strip=False):
    """The secret `cred` names, for the account (or setting) `who`, which only error messages
    show. `strip` takes the spaces out of an op read (a Gmail app password as 1Password keeps it).
    Raises when it can't be had."""
    ref = CredentialRef.parse(cred or {})
    if ref.style == "plain":
        return ref.named
    if ref.style == "keychain":
        if not (isinstance(ref.named, str) and ref.named.strip()):
            raise RuntimeError(f"no credential configured for {who}")
        return _keychain_read(ref.named, who)
    if ref.style == "env":
        v = environment.credential(ref.named)
        if not v:
            raise RuntimeError(f"env var {ref.named} not set for {who}")
        return v
    if ref.style == "proton_bridge":
        return bridge_password()
    if ref.style == "op":
        last = RuntimeError("no op refs")
        for one in ref.op_refs:
            try:
                return _op_read(one, strip)
            except Exception as e:
                last = e
        raise last
    raise RuntimeError(f"no credential configured for {who}")


def evict(cred):
    """Drop any cached reads for `cred`: its op refs, or its Keychain item."""
    ref = CredentialRef.parse(cred)
    if ref.style == "op":
        for one in ref.op_refs:
            _op_cache.pop(one, None)
    if ref.style == "keychain" and isinstance(ref.named, str):
        _keychain_cache.pop(ref.named, None)


def forget():
    """Drop every cached read, the Bridge password's too, so each secret is had afresh."""
    _op_cache.clear()
    _keychain_cache.clear()
    _bridge.clear()


def bridge_password():
    if "pw" in _bridge:
        return _bridge["pw"]
    pw = environment.PROTON_BRIDGE_PASS.value()
    if not pw:
        item = environment.PROTON_OP_ITEM.value()
        if item:
            r = subprocess.run(["op", "read", f"op://Private/{item}/password"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                pw = r.stdout.strip()
    if not pw and BRIDGE_FILE.exists():
        pw = BRIDGE_FILE.read_text().strip()
    if not pw:
        raise RuntimeError(
            f"Proton Bridge password not configured. Set {environment.PROTON_BRIDGE_PASS.name}, "
            f"or {environment.PROTON_OP_ITEM.name} (a 1Password item id), or write it to "
            f"{BRIDGE_FILE} (chmod 600).")
    _bridge["pw"] = pw
    return pw
