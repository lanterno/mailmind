"""Resolve the Proton Mail Bridge password without hard-coding it in source.

Resolution order (first hit wins):
  1. env  MAILMIND_PROTON_BRIDGE_PASS
  2. 1Password  op://Private/<MAILMIND_PROTON_OP_ITEM>/password   (if env set)
  3. file  ~/.config/mailmind/proton_bridge_pass   (chmod 600)

To get full `op`/Touch-ID parity like the Gmail accounts: store the Bridge
password in a 1Password item and `export MAILMIND_PROTON_OP_ITEM=<item-id>`.
The Bridge password is account-specific and rotatable in the Bridge app, so it
is not as sensitive as a primary credential — but it no longer lives in code.
"""
import os
import subprocess
import pathlib

SECRET_FILE = pathlib.Path.home() / ".config" / "mailmind" / "proton_bridge_pass"
_cache = {}


def proton_bridge_pass():
    if "pw" in _cache:
        return _cache["pw"]
    pw = os.environ.get("MAILMIND_PROTON_BRIDGE_PASS")
    if not pw:
        item = os.environ.get("MAILMIND_PROTON_OP_ITEM")
        if item:
            r = subprocess.run(["op", "read", f"op://Private/{item}/password"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                pw = r.stdout.strip()
    if not pw and SECRET_FILE.exists():
        pw = SECRET_FILE.read_text().strip()
    if not pw:
        raise RuntimeError(
            "Proton Bridge password not configured. Set MAILMIND_PROTON_BRIDGE_PASS, "
            "or MAILMIND_PROTON_OP_ITEM (a 1Password item id), or write it to "
            f"{SECRET_FILE} (chmod 600).")
    _cache["pw"] = pw
    return pw
