"""mailmind.accounts.domain.health: what kind of trouble an error names, from its class alone, and how an account stands."""


from fixtures import world as w


def store_write():
    from mailmind.bootstrap import mailbox
    return mailbox._uow.write()


def test_health_reads_only_the_inbox_row_and_what_a_sync_is_doing_now(app, world):
    from mailmind.bootstrap import accounts, mailbox
    mailbox.syncing.sync_accounts({w.ADA})
    with store_write() as c:
        c.execute("INSERT OR REPLACE INTO sync_state(account, folder, last_ok_ts, sync_count) VALUES(?, 'Archive', 'then', 9)", (w.ADA,))
    mine = [a for a in accounts.health.answer()["accounts"] if a["account"] == w.ADA]
    assert [(a["sync_count"], a["live"], a["healthy"]) for a in mine] == [(1, "ok", True)]


def test_an_error_shows_by_class_and_kind_and_says_nothing_else(app, world):
    from mailmind.bootstrap import accounts, mailbox
    world.server.fail("LOGIN", "NO [AUTHENTICATIONFAILED] hunter2", user=w.ADA, times=None)
    mailbox.syncing.sync_accounts({w.ADA})
    mine = next(a for a in accounts.health.answer()["accounts"] if a["account"] == w.ADA)
    assert (mine["last_error"], mine["last_error_kind"], mine["live"], mine["healthy"]) == ("LoginUnreachable", "AUTH", "error", False)
    assert "hunter2" not in str(mine)
