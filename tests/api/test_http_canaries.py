"""The world's canaries (support/canaries.py) are looked for in what the server says, whichever server it is: a password
written into its output on purpose is found, by name and never shown, as the test ends."""
import pytest

import canaries
from fixtures import world as w


def test_what_the_server_says_is_watched_for_the_canaries(app, world):
    watch = canaries.current()
    assert str(app.log_path) in watch.logs and watch.home == str(world.dir)
    with open(app.log_path, "rb") as log:
        said = log.read()
    with open(app.log_path, "ab") as log:                 # the server appends to it, so a line added here stays
        log.write(b"logged in with " + w.PASSWORDS[w.CY].encode() + b"\n")
    with pytest.raises(AssertionError) as raised:
        watch.check()
    assert "Cy's account password in " + str(app.log_path) in str(raised.value)
    assert w.PASSWORDS[w.CY] not in str(raised.value)
    with open(app.log_path, "r+b") as log:                # and taken out, so the world's own check finds it clean
        log.truncate(len(said))
    watch.check()
