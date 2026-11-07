"""uvicorn as `python -m mailmind serve` runs it: on 127.0.0.1 only, quiet, and quick to stop."""
import uvicorn

# On shutdown, how long requests still being answered get to finish before they are cancelled.
GRACE_SECONDS = 3


def config(app, *, port, grace=GRACE_SECONDS):
    """No access log, and no Server or Date header of uvicorn's:
    the bridged handler sends its own."""
    return uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                          server_header=False, date_header=False, timeout_graceful_shutdown=grace)


class Server(uvicorn.Server):
    """uvicorn's server, awaiting `before_shutdown()` as soon as shutdown begins. uvicorn waits for
    every open response before it stops, and a stream that never ends on its own would run out the
    whole grace period and then be cancelled mid-write."""

    def __init__(self, config, before_shutdown=None):
        super().__init__(config)
        self._before_shutdown = before_shutdown

    async def shutdown(self, sockets=None):
        if self._before_shutdown is not None:
            await self._before_shutdown()
        await super().shutdown(sockets=sockets)
