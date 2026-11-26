"""mmtest: the end-to-end framework of docs/test-architecture.md, as a pytest plugin (plugin.py) and as the `mm` command
(cli.py). Scenarios live in tests/scenarios/<feature>/ and read:

    from mmtest import clients, mail

    @clients("page")
    def test_words_typed_in_a_reply_all_go_above_the_signature(world, app):
        ...

The parts: world.py (the world over tests/fakes, the server or MailMind Dev, the test clock), page.py (the page client
and settling), mac.py (MailMind Dev through its driver, lane A), shown.py (what a person sees, as text), screens.py (the
screens scenarios call), trace.py (the text-first trace) and session.py (a world and a client kept up for `mm`).
"""
import pytest

from . import mail

KNOWN_CLIENTS = ("page", "mac")


def clients(*names):
    """Run the scenario on these clients: "page" and "mac" (MailMind Dev, lane A). Only a rule both clients share runs
    on both. Without it a scenario runs on the page."""
    unknown = [n for n in names if n not in KNOWN_CLIENTS]
    if unknown or not names:
        raise ValueError(f"clients are {', '.join(KNOWN_CLIENTS)}: not {unknown or 'none'}")
    return pytest.mark.clients(*names)


def known_bug(client, why):
    """A known bug on one client: the scenario states the right behaviour, and runs there as `xfail(strict=True)`, so it
    fails the run the day the bug is fixed and the mark is left behind."""
    if client not in KNOWN_CLIENTS or not why:
        raise ValueError(f"name a client ({', '.join(KNOWN_CLIENTS)}) and the bug")
    return pytest.mark.known_bug(client, why)


# Background jobs run by themselves, as the chaos and soak runs want: not yet (the page's server pauses them always).
free_running = pytest.mark.free_running
# Needs a real, active session (lane B): menus, activation, drag and drop, notifications, pictures.
needs_session = pytest.mark.needs_session

__all__ = ["clients", "known_bug", "mail", "free_running", "needs_session"]
