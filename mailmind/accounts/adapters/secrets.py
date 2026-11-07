"""Having an account's password, from wherever its credential says (mailmind.accounts.domain.credential).

An op read is cached for the process's life, and only when it succeeds; `evict` drops a
credential's cached reads when it is replaced or removed.

The Bridge password comes from the first of: the environment variable MAILMIND_PROTON_BRIDGE_PASS;
1Password's op://Private/<MAILMIND_PROTON_OP_ITEM>/password, when that variable names an item;
the file ~/.config/mailmind/proton_bridge_pass. It is cached once found.
"""
import os
import pathlib
import subprocess
import time

from mailmind.accounts.domain.credential import CredentialRef

BRIDGE_FILE = pathlib.Path.home() / ".config" / "mailmind" / "proton_bridge_pass"

_op_cache = {}
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


def resolve(cred, *, who, strip=False):
    """The secret `cred` names, for the account (or setting) `who`, which only error messages
    show. `strip` takes the spaces out of an op read (a Gmail app password as 1Password keeps it).
    Raises when it can't be had."""
    ref = CredentialRef.parse(cred or {})
    if ref.style == "plain":
        return ref.named
    if ref.style == "env":
        v = os.environ.get(ref.named)
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
    """Drop any cached op reads for `cred`'s refs."""
    ref = CredentialRef.parse(cred)
    if ref.style == "op":
        for one in ref.op_refs:
            _op_cache.pop(one, None)


def bridge_password():
    if "pw" in _bridge:
        return _bridge["pw"]
    pw = os.environ.get("MAILMIND_PROTON_BRIDGE_PASS")
    if not pw:
        item = os.environ.get("MAILMIND_PROTON_OP_ITEM")
        if item:
            r = subprocess.run(["op", "read", f"op://Private/{item}/password"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                pw = r.stdout.strip()
    if not pw and BRIDGE_FILE.exists():
        pw = BRIDGE_FILE.read_text().strip()
    if not pw:
        raise RuntimeError(
            "Proton Bridge password not configured. Set MAILMIND_PROTON_BRIDGE_PASS, "
            "or MAILMIND_PROTON_OP_ITEM (a 1Password item id), or write it to "
            f"{BRIDGE_FILE} (chmod 600).")
    _bridge["pw"] = pw
    return pw
