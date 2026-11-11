"""A line break in what a request names can't forge a line of the log."""
from fixtures import world as w
from waiting import wait_until


def test_hwf_a_folder_name_in_a_request_cant_forge_a_log_line(app, world, capsys):
    app.get(f"/api/messages?account={w.ADA}&folder=Gone%0A%5Bsync%5D%20forged&limit=5")
    out = []

    def logged():
        out.append(capsys.readouterr().out)
        return "[folder-sync]" in "".join(out)
    assert wait_until(logged)
    assert not any(line.startswith("[sync] forged") for line in "".join(out).splitlines())
