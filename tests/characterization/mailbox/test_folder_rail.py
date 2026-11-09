"""The folder rail: one LIST for a burst and for its time to live, and the last list when a refresh fails or is refused."""
import time
from datetime import timedelta

import gate
from fixtures import world as w
from world_helpers import count


def test_hwf_a_burst_of_folder_requests_lists_the_folders_once(app, world):
    import threading
    answers = []
    clients = [threading.Thread(target=lambda: answers.append(app.get(f"/api/folders?account={w.ADA}").status))
               for _ in range(4)]
    with gate.held_elsewhere():                              # every request queues on the cold cache
        for t in clients:
            t.start()
        time.sleep(0.3)
    for t in clients:
        t.join(30)
    assert answers == [200] * 4
    assert len(world.server.commands_for(w.ADA, "LIST")) == 1


def test_a_refused_folder_list_keeps_the_last_list(app, world):
    first = app.get(f"/api/folders?account={w.ADA}").json["folders"]
    app.clock.shift(timedelta(minutes=1))
    world.server.fail("LIST", "NO [UNAVAILABLE] busy", user=w.ADA)
    assert app.get(f"/api/folders?account={w.ADA}").json["folders"] == first


def folders(app):
    return app.get(f"/api/folders?account={w.ADA}").json["folders"]


def test_hwf_the_folder_rail_lists_the_server_once_per_ttl_and_serves_stale_on_failure(app, world):
    n = count(world, "LIST")
    first = folders(app)
    assert folders(app) == first and count(world, "LIST") == n + 1     # within the TTL: no new login, no LIST
    app.clock.shift(timedelta(seconds=26))
    world.server.drop("LIST", user=w.ADA)
    assert folders(app) == first                                     # the refresh failed: the last list is served
    assert count(world, "LIST") == n + 2
