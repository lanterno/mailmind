"""MailMind account configuration.

Accounts live in `mailmind.config.json` (NOT git-tracked). Copy
`mailmind.config.example.json` to that name and fill it in. Each account:

  { "email": "...", "type": "gmail|imap|proton",
    "host": "...", "port": 993, "starttls": false, "trash": "Trash",
    "credential": { ... one of the styles below ... } }

An optional top-level "anthropic_api_key" holds the Claude API key, in any of the same styles
(proton_bridge aside). It wins over ANTHROPIC_API_KEY in the environment.

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
import tempfile
import threading

import mailsecrets
from mailmind.platform import settings

# A path pinned here wins over the settings'. Only the legacy test suites still pin one.
HERE = None                 # where save() writes its temp file; None: beside the config
CONFIG = None
_cred_cache = {}
_write_lock = threading.Lock()

TYPE_DEFAULTS = {
    "gmail":  {"host": "imap.gmail.com", "port": 993, "starttls": False,
               "trash": "[Gmail]/Trash", "strip_spaces": True,
               "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_starttls": True},
    "imap":   {"host": None, "port": 993, "starttls": False,
               "trash": "Trash", "strip_spaces": False,
               "smtp_host": None, "smtp_port": 587, "smtp_starttls": True},
    "proton": {"host": "127.0.0.1", "port": 1143, "starttls": True,
               "trash": "Trash", "strip_spaces": False,
               "smtp_host": "127.0.0.1", "smtp_port": 1025, "smtp_starttls": True},
}
# SMTP transport keys editable per account; smtp_host=None means "reuse the IMAP host".
_SMTP_KEYS = ("smtp_host", "smtp_port", "smtp_starttls")


def config_path():
    """mailmind.config.json in the data directory (settings.current().home)."""
    return CONFIG or settings.current().config


def _raw():
    path = config_path()
    if not os.path.exists(path):
        return {"accounts": []}
    with open(path) as f:
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
            # SMTP transport: explicit override, else the type default; for generic
            # IMAP the default smtp_host is None, so fall back to the IMAP host.
            "smtp_host": a.get("smtp_host") or d["smtp_host"] or host,
            "smtp_port": a.get("smtp_port", d["smtp_port"]),
            "smtp_starttls": a.get("smtp_starttls", d["smtp_starttls"]),
            "alias": (a.get("alias") or "").strip() or None,
            "credential": a.get("credential", {}),
        })
    return out


def alias_map():
    """email -> alias for ALL accounts (incl. disabled), so historical DB rows
    still render with their friendly name. Built from the raw file."""
    return {e["email"]: ((e.get("alias") or "").strip() or None)
            for e in _raw().get("accounts", []) if e.get("email")}


def signature_default():
    """The global default signature (used when an account has no per-account one)."""
    return _clean_signature(_raw().get("signature_default") or "")


def set_signature_default(text):
    with _write_lock:
        raw = _raw()
        sg = _clean_signature(text)
        if sg:
            raw["signature_default"] = sg
        else:
            raw.pop("signature_default", None)
        save(raw)


def signature_map():
    """email -> effective signature (per-account if set, else the global default), for ALL
    raw accounts so the composer can insert/swap without an extra round trip."""
    default = signature_default()
    return {e["email"]: (_clean_signature(e.get("signature")) or default)
            for e in _raw().get("accounts", []) if e.get("email")}


def webmail_map():
    """email -> webmail base URL (optional per-account 'webmail' key), used as a
    best-effort 'Open original' target for IMAP accounts (e.g. Nextcloud Mail)."""
    return {e["email"]: (e.get("webmail") or "").strip() or None
            for e in _raw().get("accounts", []) if e.get("email")}


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


def anthropic_key():
    """The Claude API key from the top-level "anthropic_api_key" (any credential style above),
    or None when the file sets none. Raises when it is set but can't be resolved."""
    cred = _raw().get("anthropic_api_key")
    if not cred:
        return None
    return resolve_credential({"email": "anthropic_api_key", "credential": cred}).strip()


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


# --------------------------------------------------------------------- masking
def mask_credential(cred):
    """Public, secret-FREE descriptor of a credential. NEVER returns a value,
    length, prefix, or hash — only the style and whether something is set."""
    if isinstance(cred, str):
        return {"style": "plain", "set": bool(cred)}
    if not isinstance(cred, dict) or not cred:
        return {"style": None, "set": False}
    if "plain" in cred:
        return {"style": "plain", "set": bool(cred.get("plain"))}
    if "op" in cred:
        return {"style": "op", "set": bool(cred["op"]), "ref": cred["op"]}  # ref is a pointer, safe
    if "env" in cred:
        return {"style": "env", "set": bool(cred["env"]), "var": cred["env"]}
    if cred.get("proton_bridge"):
        return {"style": "proton_bridge", "set": True}
    return {"style": "unknown", "set": False}


def masked_row(entry):
    """Re-shape ONE raw account entry into the secret-free row the API returns.
    Echoes effective host/port/etc (with a defaults_applied flag) and masks the
    credential. The handler must NEVER serialize a raw entry directly."""
    t = entry.get("type", "imap")
    d = TYPE_DEFAULTS.get(t, TYPE_DEFAULTS["imap"])
    applied = {k: (k not in entry) for k in ("host", "port", "starttls", "trash", *_SMTP_KEYS)}
    eff_host = entry.get("host", d["host"])
    return {
        "email": entry.get("email"), "type": t,
        "alias": (entry.get("alias") or "").strip() or None,
        "host": eff_host, "port": entry.get("port", d["port"]),
        "starttls": entry.get("starttls", d["starttls"]), "trash": entry.get("trash", d["trash"]),
        "smtp_host": entry.get("smtp_host") or d["smtp_host"] or eff_host,
        "smtp_port": entry.get("smtp_port", d["smtp_port"]),
        "smtp_starttls": entry.get("smtp_starttls", d["smtp_starttls"]),
        "disabled": bool(entry.get("disabled")),
        "defaults_applied": applied,
        "credential": mask_credential(entry.get("credential")),
        "signature": (entry.get("signature") or ""),   # plain text, intentionally non-secret
    }


def masked_rows():
    return [masked_row(e) for e in _raw().get("accounts", [])]


# --------------------------------------------------------------- safe writes
def save(raw):
    """Atomically persist the whole config. Validates, fsyncs, chmod 600 before
    an atomic rename so a crash can never leave a partial/world-readable file."""
    if not (isinstance(raw, dict) and isinstance(raw.get("accounts"), list)):   # not an assert: -O strips those
        raise ValueError("bad config shape")
    blob = json.dumps(raw, indent=2, ensure_ascii=False)
    json.loads(blob)  # round-trip validation
    path = config_path()
    fd, tmp = tempfile.mkstemp(dir=HERE or os.path.dirname(path), prefix=".cfg-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _find(raw, email):
    el = (email or "").strip().lower()
    for e in raw.get("accounts", []):
        if (e.get("email") or "").strip().lower() == el:
            return e
    return None


def _evict_cred(cred):
    """Drop any cached op-read values for the given credential's refs."""
    if isinstance(cred, dict) and "op" in cred:
        refs = cred["op"] if isinstance(cred["op"], list) else [cred["op"]]
        for r in refs:
            _cred_cache.pop(r, None)


def _cred_from_payload(p):
    """Build a stored credential dict from a request payload {style,value|ref|var}.
    Returns None to mean 'keep existing'. Raises ValueError on invalid input."""
    if not p:
        return None
    style = p.get("style")
    if style == "plain":
        v = p.get("value")
        if not v:  # never blank out / store an empty password
            raise ValueError("password required")
        return {"plain": v}
    if style == "op":
        ref = p.get("ref")
        if not ref or (isinstance(ref, str) and not ref.strip()):
            raise ValueError("1Password reference required")
        return {"op": ref}
    if style == "env":
        var = (p.get("var") or "").strip()
        if not var:
            raise ValueError("environment variable name required")
        return {"env": var}
    if style == "proton_bridge":
        return {"proton_bridge": True}
    return None  # style unchanged / not provided -> keep existing


def _clean_alias(a):
    if a is None:
        return None
    a = "".join(ch for ch in str(a) if ch == " " or ch.isprintable())
    return a.strip()[:64]


def _clean_signature(s):
    """Signatures are multiline plain text (non-secret) inserted into outgoing bodies.
    Preserve newlines + printable chars; strip control/bidi/zero-width smuggling; cap length."""
    if s is None:
        return ""
    out = "".join(ch for ch in str(s) if ch in ("\n", "\r", "\t") or ch.isprintable())
    return out.strip()[:2000]


def _apply_fields(entry, p):
    """Apply editable non-credential fields from payload p onto entry in place."""
    if "alias" in p:
        al = _clean_alias(p.get("alias"))
        if al:
            entry["alias"] = al
        else:
            entry.pop("alias", None)  # blank => unset (don't store "")
    if "signature" in p:
        sg = _clean_signature(p.get("signature"))
        if sg:
            entry["signature"] = sg
        else:
            entry.pop("signature", None)  # blank => unset
    for k in ("type", "host", "trash", "smtp_host"):
        if k in p and p[k] not in (None, ""):
            entry[k] = p[k]
    for k in ("port", "smtp_port"):
        if k in p and p[k] not in (None, ""):
            entry[k] = int(p[k])
    for k in ("starttls", "strip_spaces", "disabled", "smtp_starttls"):
        if k in p and isinstance(p[k], bool):
            entry[k] = p[k]


def _validate_new(entry):
    email = (entry.get("email") or "").strip()
    if "@" not in email:
        raise ValueError("a valid email is required")
    t = entry.get("type", "imap")
    if t not in TYPE_DEFAULTS:
        raise ValueError("type must be gmail, imap, or proton")
    if t == "imap" and not (entry.get("host") or "").strip():
        raise ValueError("a host is required for a generic IMAP account")
    if "port" in entry and not (1 <= int(entry["port"]) <= 65535):
        raise ValueError("port must be 1–65535")


def add_account(p):
    with _write_lock:
        raw = _raw()
        raw.setdefault("accounts", [])
        email = (p.get("email") or "").strip()
        if _find(raw, email):
            raise ValueError(f"{email} already exists")
        entry = {"email": email, "type": p.get("type", "imap")}
        _apply_fields(entry, p)
        cred = _cred_from_payload(p.get("credential"))
        if cred is None:
            raise ValueError("a credential is required")
        entry["credential"] = cred
        _validate_new(entry)
        raw["accounts"].append(entry)
        save(raw)
        return masked_row(entry)


def update_account(email, p):
    with _write_lock:
        raw = _raw()
        entry = _find(raw, email)
        if entry is None:
            return None
        old_cred = entry.get("credential")
        _apply_fields(entry, p)
        if "credential" in p:
            new = _cred_from_payload(p["credential"])
            if new is not None:          # only replace when a real value/ref/var was given
                entry["credential"] = new
                _evict_cred(old_cred)
        _validate_new(entry)
        save(raw)
        return masked_row(entry)


def remove_account(email):
    with _write_lock:
        raw = _raw()
        entry = _find(raw, email)
        if entry is None:
            return False
        _evict_cred(entry.get("credential"))
        raw["accounts"] = [e for e in raw["accounts"] if e is not entry]
        save(raw)
        return True


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
        save(raw)
    print(f"done — {changed} account(s) now self-contained in {os.path.basename(config_path())}")


def check():
    """Resolve every account's credential and report ok/fail WITHOUT printing secrets."""
    for a in accounts():
        try:
            v = resolve_credential(a)
            print(f"  ok    {a['email']:42} ({len(v)} chars)")
        except Exception as e:
            print(f"  FAIL  {a['email']:42} {e}")
    if _raw().get("anthropic_api_key"):
        try:
            print(f"  ok    {'anthropic_api_key':42} ({len(anthropic_key() or '')} chars)")
        except Exception as e:
            print(f"  FAIL  {'anthropic_api_key':42} {e}")


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
