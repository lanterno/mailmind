"""What a test of an account's login that didn't work says to a person: a sentence that says what went wrong and what to do
next, and the page that helps, by the kind of trouble `conn_error.quiet` named. The class stays in the answer's `error`,
for the owner.

The words come from `quiet`'s, never a server's or an error's, so both servers say the same of the same trouble: a password
refused (and the provider's page where an app password is made), a server that didn't answer, a certificate not trusted,
Proton Bridge not running. A trouble at the outgoing (SMTP) server, met only once the incoming one let MailMind in, says
reading mail works. The Rust core's `mm_accounts::login_words` says the same.
"""
from dataclasses import dataclass

from mailmind.accounts.domain import conn_error, preset
from mailmind.accounts.domain.account import PORT_SAID
from mailmind.accounts.domain.preset import Link, Provider

_GMAIL_APP_PASSWORD = (conn_error.GMAIL_REFUSED, conn_error.SAID[conn_error.APP_PASSWORD])
_GMAIL_ELSE = (conn_error.SAID[conn_error.WEB_SIGN_IN], conn_error.SAID[conn_error.IMAP_OFF])
_NO_ANSWER = ("connection refused", "timeout", "host not found")


@dataclass(frozen=True)
class Said:
    says: str
    link: Link | None = None


def _provider(kind: str | None, host: object, email: object) -> Provider | None:
    """The provider an account is at: by its type, then its IMAP host, then its address's domain."""
    if kind in ("gmail", "proton"):
        return next(p for p in preset.PROVIDERS if p.preset == kind)
    found = preset.provider_of_host(host) if isinstance(host, str) else None
    domain = preset.domain_of(email) if isinstance(email, str) else None
    return found or (preset.provider_of_domain(domain) if domain else None)


def said_of(error: str, kind: str | None, host: object, email: object, outgoing: bool = False) -> Said:
    """What a test's trouble says: `error` is `quiet`'s word for it, at the incoming server, or at the outgoing one when
    `outgoing`."""
    at = _provider(kind, host, email)
    proton = at is not None and at.preset == "proton"
    if error in _GMAIL_APP_PASSWORD:
        return Said(error, preset.GOOGLE_APP_PASSWORDS)
    if error in _GMAIL_ELSE:
        return Said(error)
    if error == PORT_SAID["port"]:
        return Said("The port must be a number from 1 to 65535.")
    if error == PORT_SAID["smtp_port"]:
        return Said("The outgoing server's port must be a number from 1 to 65535.")
    if error == "connection refused" and proton and not outgoing:
        return Said("Proton Bridge isn't running. Open Bridge and wait until it says it is connected, then test again.",
                    preset.PROTON_BRIDGE)
    if error == "certificate changed":
        return Said("The mail server on this Mac showed a different certificate, so MailMind didn't send your password. If "
                    "you reinstalled or reset Proton Bridge, trust the new one in Settings.")
    if error == "AUTHENTICATIONFAILED":
        if outgoing:
            return Said("Reading mail works, but the outgoing server didn't accept the password. Check the outgoing "
                        "server's settings.")
        if proton:
            return Said("Proton Bridge didn't accept that password. Use the password Bridge shows for this address, not "
                        "your Proton password.", preset.PROTON_BRIDGE)
        if at is not None and at.settings is None:      # Microsoft's: its own sign-in, which no password stands in for
            return Said(at.note or "")
        if at is not None and at.link is not None:
            return Said(f"{at.name} didn't accept that password. {at.name} takes an app password, not your account's own "
                        "password: make one, then paste it here.", at.link)
        return Said("The server didn't accept that address and password. Check them; if your provider offers app "
                    "passwords, make one and paste it here.")
    if error in _NO_ANSWER:
        if outgoing:
            return Said("Reading mail works, but the outgoing server didn't answer. Check its name and port.")
        return Said("The server didn't answer. Check the server's name and port, and that this Mac is online.")
    if error == "SSLCertVerificationError":
        if outgoing:
            return Said("Reading mail works, but the outgoing server's certificate isn't trusted, so MailMind didn't send "
                        "your password. Check its name.")
        return Said("The server's certificate isn't trusted, so MailMind didn't send your password. Check the server's "
                    "name: use the one your provider gives.")
    if error == "SSLError":
        if outgoing:
            return Said("Reading mail works, but a secure connection to the outgoing server couldn't be made. Check its "
                        "port and whether it wants TLS or STARTTLS.")
        return Said("A secure connection couldn't be made. Check the port, and whether the server wants TLS or STARTTLS.")
    if outgoing:
        return Said("Reading mail works, but MailMind couldn't log in to the outgoing server. Check its settings.")
    return Said("MailMind couldn't log in. Check the address, the password and the server's settings.")
