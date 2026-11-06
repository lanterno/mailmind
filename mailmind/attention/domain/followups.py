"""How long a thread may wait before it asks for you, and for how long it may ask."""
GRACE_DAYS = 3      # grace window before a sent-no-reply / unanswered thread surfaces as "Waiting"
EXPIRE_DAYS = 14    # cap how long a received nudge nags (a reply from another client is locally invisible)
