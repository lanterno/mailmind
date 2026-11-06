"""What kind of trouble a sync had."""


def kind_of(e: BaseException) -> str:
    """Coarse, scrubbed error kind for the health surface — derived from the exception TYPE only,
    never its message (which can carry credentials/host strings)."""
    n = type(e).__name__.lower()
    if n == "tlspinmismatch":
        return "TLS_PIN"
    if "timeout" in n:
        return "TIMEOUT"
    if any(w in n for w in ("auth", "login", "permission")):
        return "AUTH"
    if "uidvalidity" in n:
        return "UIDVALIDITY"
    if any(w in n for w in ("gaierror", "connection", "socket", "ssl", "dns", "resolve")):
        return "NETWORK"
    if "imap" in n:
        return "IMAP"
    return "UNKNOWN"
