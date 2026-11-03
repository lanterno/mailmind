"""Phase 0 hardening: live security and data-loss fixes, each pinned by a test. Hermetic: a temp
database and config, fake IMAP and SMTP connections, and fictional addresses, so nothing here
can reach a real server.
Run: python3 test_hardening.py"""
import json
import os
import ssl
import tempfile

import store
import config
_TMP = tempfile.mkdtemp()
store.DB = os.path.join(_TMP, "mailmind.db")        # before anything opens a connection
config.CONFIG = os.path.join(_TMP, "mailmind.config.json")
with open(config.CONFIG, "w") as f:
    json.dump({"accounts": [{"email": "ada@example.test", "type": "imap",
                             "host": "imap.example.test"}]}, f)
import accounts
import smtpsend


class _FakeIMAP:
    """Stands in for imaplib.IMAP4: records the TLS context STARTTLS was given."""
    made = []

    def __init__(self, host, port, timeout=None):
        self.host, self.ctx = host, None
        _FakeIMAP.made.append(self)

    def starttls(self, ctx):
        self.ctx = ctx

    def login(self, user, pw):
        return "OK", [b"logged in"]


def _starttls_ctx(host):
    real_imap, real_cred = accounts.imaplib.IMAP4, accounts.config.resolve_credential
    accounts.imaplib.IMAP4 = _FakeIMAP
    accounts.config.resolve_credential = lambda acct: "app-password"
    try:
        accounts.connect({"email": "ada@example.test", "host": host, "port": 143, "starttls": True})
    finally:
        accounts.imaplib.IMAP4, accounts.config.resolve_credential = real_imap, real_cred
    return _FakeIMAP.made[-1].ctx


def test_starttls_off_this_machine_verifies_the_certificate():
    for host in ("imap.example.test", "192.0.2.10", "127.0.0.1.example.test"):
        ctx = _starttls_ctx(host)
        assert ctx.verify_mode == ssl.CERT_REQUIRED, host
        assert ctx.check_hostname, host


def test_starttls_on_loopback_accepts_bridges_own_certificate():
    for host in ("127.0.0.1", "localhost", "::1", " LOCALHOST "):
        ctx = _starttls_ctx(host)
        assert ctx.verify_mode == ssl.CERT_NONE, host
        assert not ctx.check_hostname, host


def test_smtp_follows_the_same_loopback_rule():
    assert smtpsend._ctx("smtp.example.test").verify_mode == ssl.CERT_REQUIRED
    assert smtpsend._ctx("smtp.example.test").check_hostname
    assert smtpsend._ctx("127.0.0.1").verify_mode == ssl.CERT_NONE


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
        except Exception as e:
            failed += 1
            print("ERR ", fn.__name__, repr(e))
    print("\n%d/%d passed" % (len(fns) - failed, len(fns)))
    sys.exit(1 if failed else 0)
