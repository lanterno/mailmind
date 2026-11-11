"""A click on a server that has hung: an answer within a moment, so Undo is offered, and the worker takes the command up
later, the way a try that lost its connection leaves one. The try gives up on a socket timeout, so a test lowers the seconds
it waits and leaves the rest of the path as it is."""


from mailmind.bootstrap import mailbox


def test_a_try_waits_for_a_hung_server_no_longer_than_a_person_will():
    assert mailbox.commands.FILE_WAIT + mailbox.commands.TRY_TIMEOUT <= 15
