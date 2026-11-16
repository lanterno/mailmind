"""The AI providers a person may choose (#49). Here because a person's preferences keep the one they chose and Accounts keeps
each one's key, while the Assistant talks to it, and neither context imports the other. A request's schema takes them as
`AiProvider`, so the page has them as a type and keeps only its words for each.

The rules a provider's base URL keeps are here for the same reason: the preferences hold a URL a person gives to them, and
the Assistant reads which URL names this machine."""
from typing import Literal, get_args
from urllib.parse import urlsplit

type AiProvider = Literal["anthropic", "openai", "gemini", "local", "compatible"]     # local: a model on this Mac
AI_PROVIDERS: tuple[AiProvider, ...] = get_args(AiProvider.__value__)
DEFAULT_PROVIDER: AiProvider = "anthropic"

BASE_URL_MAX = 500                                          # characters in a service's URL
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})     # the hosts that name this machine, written just so


def _host(url: str) -> tuple[bool, str] | None:
    """(whether it is https, its host) for a URL in printable ASCII whose scheme is https or http, with no user or password,
    query or fragment, and a port from 1 to 65535 where it names one. None for any other URL, or text that is none."""
    if any(not "!" <= ch <= "~" for ch in url) or "?" in url or "#" in url:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port                               # a port that is no number, or past 65535, raises
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or "@" in parts.netloc or not parts.hostname \
            or parts.netloc.endswith(":") or port == 0:     # "host:" names no port
        return None
    return parts.scheme == "https", parts.hostname


def base_url(given: str) -> str | None:
    """`given` as an AI service's base URL, without its trailing /: at most BASE_URL_MAX characters, https to a host or http
    to this machine. None for any other."""
    if len(given) > BASE_URL_MAX:
        return None
    named = _host(given)
    if named is None or not (named[0] or named[1] in LOOPBACK):
        return None
    return given.rstrip("/")


def on_this_machine(url: str) -> bool:
    """Whether a URL that can be a service's names this machine: 127.0.0.1, ::1 or localhost, over http or https."""
    named = _host(url)
    return named is not None and named[1] in LOOPBACK
