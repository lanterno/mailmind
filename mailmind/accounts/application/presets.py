"""The preset an address's account takes, as Settings asks while the address is typed (mailmind.accounts.domain.preset)."""
from typing import Any

from mailmind.accounts.application.ports import MailHosts
from mailmind.accounts.domain import preset
from mailmind.kernel.errors import Refused


class Presets:
    def __init__(self, mail_hosts: MailHosts):
        self._mail_hosts = mail_hosts

    def preset(self, email: str | None) -> dict[str, Any]:
        """A provider's preset, `sure` for an address at one of its own domains, which the form chooses, and offered
        (`sure` false) for one whose domain's MX records name its servers; none for any other, a domain no lookup could
        ask about, or one whose lookup found nothing. No address at all is a bad request."""
        if not (email or "").strip():
            raise Refused()
        domain = preset.domain_of(email or "")
        if domain is None:
            return _answer(None, None, False)
        own = preset.provider_of_domain(domain)
        if own:
            return _answer(domain, own, True)
        hosts = self._mail_hosts.of(domain)
        offered = next((p for p in preset.PROVIDERS if any(preset.names_provider(h, p) for h in hosts)), None)
        return _answer(domain, offered, False)


def _answer(domain: str | None, found: preset.Provider | None, sure: bool) -> dict[str, Any]:
    elsewhere = found.custom_domain if found and not sure else None   # signed in by another address: nothing to save
    settings = found.settings if found and not elsewhere else None
    return {"ok": True, "domain": domain, "preset": found and found.preset, "sure": sure,
            "name": found and found.name, "supported": found is None or settings is not None,
            "settings": preset.settings_dict(settings) if settings else None,
            "note": elsewhere or (found and found.note),
            "link": preset.link_dict(found.link if found and not elsewhere else None)}
