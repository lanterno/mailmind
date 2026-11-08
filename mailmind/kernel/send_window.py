"""How long a send waits in the outbox before it goes, so Undo Send can stop it. Here because Compose holds a send to it and a
person's preferences (Accounts) may change it, and neither context imports the other."""

DEFAULT = 10
SHORTEST = 5      # too short to reach Undo, and a send that goes at once by accident can't be taken back
LONGEST = 60      # a message held longer than this looks sent while it isn't
