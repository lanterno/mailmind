"""Which preset an address's account takes, from its domain: a provider's own domains take its preset, which the form chooses
(`sure`), and a domain whose mail a provider's servers take (a Google Workspace or Fastmail domain's, say), as its MX records
name them, is offered it, as an iCloud+ custom domain's is iCloud's. An offer is only that: the person chooses, and nothing is
guessed that they can't undo. A brand's domain is its own under `.com` or a country's ending (yahoo.fr, yahoo.co.uk), but for a
country where the brand is another company's (Yahoo Japan's yahoo.co.jp).

A preset says what the form needs of its provider: the type and servers its account takes, a line said before the password (an
app password asked for, Proton Bridge needed), and the page where that password is made. A provider MailMind can't log in to yet
(Microsoft's, which needs its own sign-in, #47) is named, and has no servers. The Rust core's `mm_accounts::presets` holds the
same table.

A domain is looked up only when it reads as one a lookup could ask about: ASCII labels of letters, digits and hyphens, two
or more of them, at most 253 characters. Anything else, a name in another script included, is no domain here, and offers
nothing.
"""
import re
from dataclasses import dataclass
from typing import Any, Literal

type Preset = Literal["gmail", "icloud", "yahoo", "aol", "fastmail", "microsoft", "proton"]

GOOGLE_DOMAINS = ("gmail.com", "googlemail.com")       # Google's own addresses: their accounts are Gmail's
LONGEST_DOMAIN = 253
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
SECOND_LEVELS = ("co", "com", "ne", "or")              # what a country's ending may put before its own (yahoo.co.uk)


@dataclass(frozen=True)
class Server:
    host: str
    port: int
    starttls: bool                                     # else TLS from the first byte


@dataclass(frozen=True)
class Settings:
    """What an account of a preset is saved as: its type, and for a generic IMAP one its servers and Trash."""
    type: str
    imap: Server | None = None
    smtp: Server | None = None
    trash: str | None = None


@dataclass(frozen=True)
class Link:
    url: str
    words: str


@dataclass(frozen=True)
class Provider:
    preset: Preset
    name: str
    domains: tuple[str, ...] = ()
    brands: tuple[str, ...] = ()                       # its own under .com or a country's ending: yahoo.com, yahoo.co.uk
    not_in: tuple[str, ...] = ()                       # countries where the brand is another company's: Yahoo Japan's
    mail_hosts: tuple[str, ...] = ()                   # an MX record naming one of these, or a host under it
    settings: Settings | None = None                   # None: MailMind can't log in to it yet
    note: str | None = None                            # what the form says before the password
    link: Link | None = None                           # where its password for MailMind is made


GOOGLE_APP_PASSWORDS = Link("https://myaccount.google.com/apppasswords", "Make an App Password")
PROTON_BRIDGE = Link("https://proton.me/mail/bridge", "Get Proton Bridge")

PROVIDERS = (
    Provider("gmail", "Gmail", domains=GOOGLE_DOMAINS, mail_hosts=("google.com", "googlemail.com"),
             settings=Settings("gmail"), link=GOOGLE_APP_PASSWORDS),
    Provider("icloud", "iCloud", domains=("icloud.com", "me.com", "mac.com"), mail_hosts=("mail.icloud.com",),
             settings=Settings("imap", Server("imap.mail.me.com", 993, False), Server("smtp.mail.me.com", 587, True),
                               "Deleted Messages"),
             note="iCloud Mail takes an app-specific password, not your Apple Account password: make one at account.apple.com, "
                  "under Sign-In and Security, then paste it here.",
             link=Link("https://account.apple.com", "Make an app-specific password")),
    Provider("yahoo", "Yahoo", domains=("ymail.com", "rocketmail.com"), brands=("yahoo",), not_in=("jp",),
             settings=Settings("imap", Server("imap.mail.yahoo.com", 993, False), Server("smtp.mail.yahoo.com", 465, False)),
             note="Yahoo Mail takes an app password, not your Yahoo password: make one in your Yahoo account's security "
                  "settings, then paste it here.",
             link=Link("https://login.yahoo.com/account/security", "Make an app password")),
    Provider("aol", "AOL", domains=("aim.com",), brands=("aol",),
             settings=Settings("imap", Server("imap.aol.com", 993, False), Server("smtp.aol.com", 465, False)),
             note="AOL Mail takes an app password, not your AOL password: make one in your AOL account's security "
                  "settings, then paste it here.",
             link=Link("https://login.aol.com/account/security", "Make an app password")),
    Provider("fastmail", "Fastmail", domains=("fastmail.com", "fastmail.fm"), mail_hosts=("messagingengine.com",),
             settings=Settings("imap", Server("imap.fastmail.com", 993, False), Server("smtp.fastmail.com", 465, False)),
             note="Fastmail takes an app password, not your Fastmail password: make one in Fastmail's settings, under "
                  "Privacy & Security, then paste it here.",
             link=Link("https://www.fastmail.help/hc/en-us/articles/360058752854", "How to make an app password")),
    Provider("microsoft", "Microsoft", domains=("msn.com", "passport.com"), brands=("outlook", "hotmail", "live"),
             mail_hosts=("protection.outlook.com",),
             note="Outlook, Hotmail, Live and Microsoft 365 accounts need Microsoft's own sign-in, which MailMind doesn't "
                  "support yet."),
    Provider("proton", "Proton", domains=("proton.me", "protonmail.com", "protonmail.ch", "pm.me"),
             mail_hosts=("protonmail.ch",), settings=Settings("proton"),
             note="Proton Mail works through Proton Bridge, on a paid Proton plan: install Bridge, sign in to it, and keep it "
                  "running while MailMind is open.",
             link=PROTON_BRIDGE),
)


def domain_of(email: str) -> str | None:
    """The domain an address names, in lower case and without a final dot, when it reads as one; else None."""
    _, at, domain = email.strip().rpartition("@")
    if not (at and domain.isascii()):             # lowered, a letter of another script may come out as an ASCII one
        return None
    domain = domain.lower().removesuffix(".")
    labels = domain.split(".")
    if len(domain) > LONGEST_DOMAIN or len(labels) < 2 or not all(_LABEL.fullmatch(one) for one in labels):
        return None
    return domain


def _is_country(ending: str) -> bool:
    """Whether `ending` is a country's: two letters."""
    return len(ending) == 2 and ending.isascii() and ending.isalpha()


def _under_brand(domain: str, brand: str, not_in: tuple[str, ...]) -> bool:
    """Whether `domain` is `brand` under `.com` or a country's ending, as brand.com, brand.fr or brand.co.uk, unless the
    country is one of `not_in`."""
    labels = domain.split(".")
    country = _is_country(labels[-1]) and labels[-1] not in not_in
    if len(labels) == 2:
        return labels[0] == brand and (labels[1] == "com" or country)
    return len(labels) == 3 and labels[0] == brand and labels[1] in SECOND_LEVELS and country


def provider_of_domain(domain: str) -> Provider | None:
    """The provider whose own domain `domain` is, if one's is."""
    return next((p for p in PROVIDERS
                 if domain in p.domains or any(_under_brand(domain, b, p.not_in) for b in p.brands)), None)


def names_provider(host: str, provider: Provider) -> bool:
    """Whether an MX record's host is one of `provider`'s mail servers."""
    host = host.lower().removesuffix(".")
    return any(host == known or host.endswith("." + known) for known in provider.mail_hosts)


def names_google(host: str) -> bool:
    """Whether an MX record's host is one of Google's mail servers."""
    return names_provider(host, PROVIDERS[0])


def provider_of_host(host: str) -> Provider | None:
    """The provider whose servers an IMAP host is, as a preset names them."""
    host = host.lower()
    return next((p for p in PROVIDERS if p.settings and p.settings.imap and p.settings.imap.host == host), None)


def settings_dict(settings: Settings) -> dict[str, Any]:
    """A preset's settings as the answer gives them: every field the account body takes, None where its type gives it."""
    imap, smtp = settings.imap, settings.smtp
    return {"type": settings.type, "host": imap and imap.host, "port": imap and imap.port,
            "starttls": imap and imap.starttls, "smtp_host": smtp and smtp.host, "smtp_port": smtp and smtp.port,
            "smtp_starttls": smtp and smtp.starttls, "trash": settings.trash}


def link_dict(link: Link | None) -> dict[str, str] | None:
    return {"url": link.url, "words": link.words} if link else None
