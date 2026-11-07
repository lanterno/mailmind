"""uvicorn as `python -m mailmind serve` runs it: on 127.0.0.1 only, quiet, and quick to stop."""
import uvicorn

from mailmind.platform import sse

# On shutdown, how long requests still being answered get to finish before they are cancelled.
GRACE_SECONDS = 3


def config(app, *, port, grace=GRACE_SECONDS):
    """No access log, and no Server or Date header: an answer says what its route gave it and what the perimeter adds."""
    return uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                          server_header=False, date_header=False, timeout_graceful_shutdown=grace)


class Server(uvicorn.Server):
    """uvicorn's server, ending the event streams as soon as shutdown begins. uvicorn waits for every open response before it
    stops, and a stream that never ends on its own would run out the whole grace period and then be cancelled mid-write."""

    async def shutdown(self, sockets=None):
        sse.HUB.end_streams()
        await super().shutdown(sockets=sockets)
