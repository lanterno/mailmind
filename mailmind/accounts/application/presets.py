"""The preset an address's account takes, as Settings asks while the address is typed (mailmind.accounts.domain.preset)."""
from typing import Any

from mailmind.accounts.application.ports import MailHosts
from mailmind.accounts.domain import preset
from mailmind.kernel.errors import Refused


class Presets:
    def __init__(self, mail_hosts: MailHosts):
        self._mail_hosts = mail_hosts

    def preset(self, email: str | None) -> dict[str, Any]:
        """Gmail's preset, `sure` for an address at one of Google's own domains, which the form chooses, and offered
        (`sure` false) for one whose domain's MX records name Google's servers; none for any other, a domain no lookup
        could ask about, or one whose lookup found nothing. No address at all is a bad request."""
        if not (email or "").strip():
            raise Refused()
        domain = preset.domain_of(email or "")
        if domain is None:
            return _answer(None, None, False)
        if domain in preset.GOOGLE_DOMAINS:
            return _answer(domain, "gmail", True)
        google = any(preset.names_google(host) for host in self._mail_hosts.of(domain))
        return _answer(domain, "gmail" if google else None, False)


def _answer(domain: str | None, found: preset.Preset | None, sure: bool) -> dict[str, Any]:
    return {"ok": True, "domain": domain, "preset": found, "sure": sure}
