"""A connection's trouble in words that are safe to show: what went wrong, never what the server or the error said."""
from mailmind.accounts.domain import tls_pin


def quiet(e):
    if isinstance(e, tls_pin.TLSPinMismatch):
        return "certificate changed"
    s = f"{type(e).__name__}: {e}".lower()
    if any(w in s for w in ("auth", "credential", "invalid", "login", "password")):
        return "AUTHENTICATIONFAILED"
    if "refused" in s:
        return "connection refused"
    if "timed out" in s or "timeout" in s:
        return "timeout"
    if any(w in s for w in ("name or service", "nodename", "getaddrinfo", "not known")):
        return "host not found"
    return type(e).__name__
