"""What a use case raises when it can't do what was asked, carrying what a person is told.

`said` is written for that person, in words of the app's own: never an exception's text, or what a server said. It may be
empty, for an answer the page words itself. `more` is what else the answer says, flags and ids a client acts on.

The kernel knows nothing of HTTP. `mailmind.http.handler` says which status each answers with, and how.

Four others keep paths of their own. `AIOff` and `OverLimit` are `NotSent`s, which the AI's callers let through by that name,
and an `OverLimit` carries what the page needs to ask. `ReadOnlyError` names what was refused, for the log, and is a member of
the command path's `UNREACHABLE`, so a move queues. `TxAcrossNetworkError` is the app's own bug, answered like any other.
"""


class Problem(Exception):
    def __init__(self, said: str = "", **more: object):
        super().__init__(said)
        self.said = said
        self.more = more


class NotFound(Problem):
    """What the request names isn't there, or isn't any more."""


class Conflict(Problem):
    """Things have moved on from what the request saw, or stand in its way. The same request again won't help."""


class Locked(Problem):
    """Something holds it for now, as a queued send holds its draft."""


class Refused(Problem):
    """The request can't go as it stands: it is malformed, or it asks what can't be."""

    def __init__(self, said: str = "bad request", **more: object):
        super().__init__(said, **more)


class Unreachable(Problem):
    """A server or the model didn't answer. Asking again can help."""


class Unreadable(Problem):
    """It came, and can't be read. Asking again won't help."""


class Busy(Problem):
    """Another job holds what this one needs, and this one never ran."""

    def __init__(self, said: str = "", **more: object):
        super().__init__(said, busy=True, **more)
