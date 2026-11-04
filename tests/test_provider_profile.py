"""The ProviderProfile (mailmind.accounts.domain.provider): what each kind of account's server does
on its own, its quirks, and what its CAPABILITY list says."""
from fixtures import world as w
from mailmind.accounts.domain import provider


def test_gmail_files_sent_mail_itself_and_archives_by_label():
    p = provider.resolve({"kind": "gmail", "host": "imap.gmail.com", "starttls": False})
    assert (p.saves_sent, p.archive_is_label_removal, p.gmail_threads) == (True, True, True)
    assert (p.trash_fallback, p.junk_fallback) == ("[Gmail]/Trash", "[Gmail]/Spam")
    assert (p.bridge, p.bulk_expunge_unsafe, p.marks_trash_read) == (False, False, False)
    assert (p.recycle_seconds, p.recycle_uses) == (600, 200)


def test_proton_bridge_is_fragile_and_reads_what_it_trashes():
    p = provider.resolve({"kind": "proton", "host": "127.0.0.1", "starttls": True})
    assert (p.bridge, p.bulk_expunge_unsafe, p.marks_trash_read) == (True, True, True)
    assert (p.recycle_seconds, p.recycle_uses) == (90, 40)
    assert (p.saves_sent, p.archive_is_label_removal, p.junk_fallback) == (False, False, None)


def test_another_server_on_loopback_behind_starttls_is_handled_like_bridge():
    for host in ("127.0.0.1", "localhost", "::1"):
        p = provider.resolve({"kind": "imap", "host": host, "starttls": True})
        assert (p.kind, p.bridge, p.recycle_seconds, p.marks_trash_read) == ("imap", True, 90, False)
    assert not provider.resolve({"kind": "imap", "host": "127.0.0.1", "starttls": False}).bridge


def test_a_plain_imap_server_has_no_quirks():
    p = provider.resolve({"kind": "imap", "host": "mail.example.test", "starttls": False})
    assert p == provider.ProviderProfile("imap")
    assert provider.resolve({}).kind == "imap"


def test_capabilities_are_unknown_until_a_server_says():
    p = provider.resolve({"kind": "imap", "host": "mail.example.test"})
    assert (p.move, p.uidplus, p.idle, p.special_use, p.condstore) == (None,) * 5
    said = provider.resolve({"kind": "imap", "host": "mail.example.test"}, ("IMAP4rev1", "move", "IDLE"))
    assert (said.move, said.uidplus, said.idle, said.special_use, said.condstore) == (True, False, True, False, False)


def test_deep_links_open_the_message_where_the_provider_can():
    gmail, proton, imap = (provider.resolve({"kind": k}) for k in ("gmail", "proton", "imap"))
    link = dict(account="cy@example.test", message_id="<abc@mail.example.test>", subject="Lunch on Thursday?")
    assert gmail.deep_link(**link) == ("https://mail.google.com/mail/u/?authuser=cy%40example.test"
                                       "#search/rfc822msgid:abc%40mail.example.test")
    assert proton.deep_link(**link) == "https://mail.proton.me/u/0/almost-all-mail#keyword=Lunch%20on%20Thursday%3F"
    assert imap.deep_link(**link) == ""
    assert imap.deep_link(**link, webmail="https://webmail.example.test/") == "https://webmail.example.test/"
    assert gmail.deep_link(**{**link, "message_id": ""}) == ""


def test_the_fake_servers_capabilities_reach_the_profile(world):
    import accounts
    acct = accounts.get(w.ADA)
    world.server.without = {"MOVE"}
    M = accounts.connect(acct, timeout=10)
    try:
        p = provider.resolve(acct, M.capabilities)
    finally:
        M.logout()
    assert (p.move, p.uidplus, p.idle, p.special_use) == (False, True, True, True)
