"""MailMind account configuration.

Accounts live in `mailmind.config.json` (NOT git-tracked). Copy
`mailmind.config.example.json` to that name and fill it in. Each account:

  { "email": "...", "type": "gmail|imap|proton",
    "host": "...", "port": 993, "starttls": false, "trash": "Trash",
    "credential": { ... one of the styles below ... } }

Credential styles (so this works for anyone, with or without 1Password):
  { "plain": "the-password" }                       inline (kept off git via .gitignore)
  { "op": "op://Vault/Item/Field" }                 read from 1Password CLI at runtime
  { "op": ["op://.../Field A", "op://.../Field B"] } try refs in order
  { "env": "ENV_VAR_NAME" }                          read from an environment variable
  { "proton_bridge": true }                          resolve via mailsecrets (Bridge password)

Type defaults (override per account as needed):
  gmail  -> imap.gmail.com:993 TLS, trash "[Gmail]/Trash", app-password spaces stripped
  imap   -> :993 TLS,            trash "Trash",          password used verbatim
  proton -> 127.0.0.1:1143 STARTTLS (local Bridge), trash "Trash"
"""
import os
import json
import subprocess
import time

import mailsecrets

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "mailmind.config.json")
_cred_cache = {}

TYPE_DEFAULTS = {
    "gmail":  {"host": "imap.gmail.com", "port": 993, "starttls": False,
               "trash": "[Gmail]/Trash", "strip_spaces": True},
    "imap":   {"host": None, "port": 993, "starttls": False,
               "trash": "Trash", "strip_spaces": False},
    "proton": {"host": "127.0.0.1", "port": 1143, "starttls": True,
               "trash": "Trash", "strip_spaces": False},
}


def _raw():
    if not os.path.exists(CONFIG):
        return {"accounts": []}
    with open(CONFIG) as f:
        return json.load(f)


def accounts():
    """Return normalized account dicts from the config (disabled ones skipped)."""
    out = []
    for a in _raw().get("accounts", []):
        if a.get("disabled"):
            continue
        t = a.get("type", "imap")
        d = TYPE_DEFAULTS.get(t, TYPE_DEFAULTS["imap"])
        host = a.get("host", d["host"])
        if not host:
            continue  # an imap account with no host can't connect
        out.append({
            "email": a["email"], "kind": t, "host": host,
            "port": a.get("port", d["port"]), "starttls": a.get("starttls", d["starttls"]),
            "trash": a.get("trash", d["trash"]), "strip_spaces": a.get("strip_spaces", d["strip_spaces"]),
            "credential": a.get("credential", {}),
        })
    return out


def _op_read(ref, strip):
    if ref in _cred_cache:
        return _cred_cache[ref]
    last = ""
    for _ in range(3):
        r = subprocess.run(["op", "read", ref], capture_output=True, text=True)
        if r.returncode == 0:
            pw = r.stdout.strip()
            if strip:
                pw = pw.replace(" ", "")
            _cred_cache[ref] = pw
            return pw
        last = r.stderr.strip()
        if "timeout" in last.lower() or "authorization" in last.lower():
            time.sleep(3)
            continue
        break
    raise RuntimeError(f"op read failed for {ref}: {last}")


def resolve_credential(acct):
    cred = acct.get("credential", {}) or {}
    strip = acct.get("strip_spaces", False)
    if isinstance(cred, str):       # shorthand: "credential": "the-password"
        return cred
    if "plain" in cred:
        return cred["plain"]
    if "env" in cred:
        v = os.environ.get(cred["env"])
        if not v:
            raise RuntimeError(f"env var {cred['env']} not set for {acct['email']}")
        return v
    if cred.get("proton_bridge"):
        return mailsecrets.proton_bridge_pass()
    if "op" in cred:
        refs = cred["op"] if isinstance(cred["op"], list) else [cred["op"]]
        last = RuntimeError("no op refs")
        for ref in refs:
            try:
                return _op_read(ref, strip)
            except Exception as e:
                last = e
        raise last
    raise RuntimeError(f"no credential configured for {acct['email']}")


def preload():
    """Resolve every op-backed credential once (one Touch ID), so a long-running
    server never has to prompt again."""
    ok = 0
    for a in accounts():
        cred = a.get("credential")
        if isinstance(cred, dict) and "op" in cred:
            try:
                resolve_credential(a)
                ok += 1
            except Exception as e:
                print(f"  ! preload failed for {a['email']}: {e}")
    return ok


# --------------------------------------------------------------------------- CLI
def _strip_for(entry):
    t = entry.get("type", "imap")
    return entry.get("strip_spaces", TYPE_DEFAULTS.get(t, TYPE_DEFAULTS["imap"])["strip_spaces"])


def materialize(only=None):
    """Rewrite op/env/proton_bridge credentials as inline {plain:...} in
    mailmind.config.json, making it fully self-contained (no 1Password needed).
    The file is git-ignored. Pass an email to convert just one account."""
    raw = _raw()
    changed = 0
    for entry in raw.get("accounts", []):
        if only and entry.get("email") != only:
            continue
        cred = entry.get("credential")
        if isinstance(cred, dict) and "plain" in cred or isinstance(cred, str):
            continue  # already inline
        try:
            pw = resolve_credential({"email": entry.get("email"), "credential": cred,
                                     "strip_spaces": _strip_for(entry)})
        except Exception as e:
            print(f"  ! skip {entry.get('email')}: {e}")
            continue
        entry["credential"] = {"plain": pw}
        changed += 1
        print(f"  materialized {entry.get('email')}")
    if changed:
        with open(CONFIG, "w") as f:
            json.dump(raw, f, indent=2, ensure_ascii=False)
    print(f"done — {changed} account(s) now self-contained in {os.path.basename(CONFIG)}")


def check():
    """Resolve every account's credential and report ok/fail WITHOUT printing secrets."""
    for a in accounts():
        try:
            v = resolve_credential(a)
            print(f"  ok    {a['email']:42} ({len(v)} chars)")
        except Exception as e:
            print(f"  FAIL  {a['email']:42} {e}")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "materialize":
        materialize(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "check":
        check()
    else:
        for a in accounts():
            c = a.get("credential")
            style = "plain" if isinstance(c, (str,)) or (isinstance(c, dict) and "plain" in c) else \
                    ("op" if isinstance(c, dict) and "op" in c else
                     "env" if isinstance(c, dict) and "env" in c else
                     "proton_bridge" if isinstance(c, dict) and c.get("proton_bridge") else "?")
            print(f"  {a['email']:42} {a['kind']:6} {a['host']}:{a['port']}  cred={style}")
