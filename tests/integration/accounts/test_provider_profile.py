"""The ProviderProfile (mailmind.accounts.domain.provider): what each kind of account's server does
on its own, its quirks, and what its CAPABILITY list says."""
from fixtures import world as w
from mailmind.accounts.domain import provider


def test_the_fake_servers_capabilities_reach_the_profile(world):
    from mailmind.bootstrap import accounts
    acct = accounts.get(w.ADA)
    world.server.without = {"MOVE"}
    M = accounts.connect(acct, timeout=10)
    try:
        p = provider.resolve(acct, M.capabilities)
    finally:
        M.logout()
    assert (p.move, p.uidplus, p.idle, p.special_use) == (False, True, True, True)
