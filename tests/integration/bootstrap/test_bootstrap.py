"""mailmind.bootstrap.wiring: what `python -m mailmind serve` and the app fixture connect before anything
runs, what serving starts, and the background jobs each profile schedules."""




def test_an_account_gets_imapclient_only_where_it_is_offered(world):
    from mailmind.bootstrap import accounts
    from fixtures import world as w
    from mailmind.bootstrap import mailbox
    w.ask_for_imapclient(world.dir, w.CY)

    def adapters():
        sessions = {who: mailbox.open_session(accounts.get(who), timeout=10) for who in (w.ADA, w.CY)}
        for s in sessions.values():
            s.logout()
        return {who: type(s).__name__ for who, s in sessions.items()}
    assert adapters() == {w.ADA: "ImaplibSession", w.CY: "ImaplibSession"}       # the command line's
    assert mailbox.describe() == "mail through imaplib"
    mailbox.offer("imapclient")                                                  # front.build's
    assert adapters() == {w.ADA: "ImaplibSession", w.CY: "ImapClientSession"}
    assert mailbox.describe() == "mail through imaplib, imapclient for 1 account that asked"
    mailbox.use("imapclient")                                                    # every account's
    assert adapters() == {w.ADA: "ImapClientSession", w.CY: "ImapClientSession"}


def test_the_app_runs_the_account_that_asks_on_imapclient_and_every_other_on_imaplib(app, world):
    from mailmind.bootstrap import accounts
    from fixtures import world as w
    from mailmind.bootstrap import mailbox
    assert (mailbox.kind_for(accounts.get(w.CY)), mailbox.kind_for(accounts.get(w.ADA))) == ("imaplib", "imaplib")
    w.ask_for_imapclient(world.dir, w.CY)
    assert (mailbox.kind_for(accounts.get(w.CY)), mailbox.kind_for(accounts.get(w.ADA))) == ("imapclient", "imaplib")
