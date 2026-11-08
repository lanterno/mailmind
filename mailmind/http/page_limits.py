"""The limits the page holds a person to before it asks, each the server's own constant, which its use case enforces.

`just gen-api` writes them into web/src/lib/api/limits.gen.ts beside the API's types (tests/openapi_doc.py), and `just check-web`
fails when that file no longer matches, so the page keeps no copy of one: a limit that changes here changes there with the next
`just gen-api`. Nothing serves them: they are fixed for a build, as the API's types are, and a tab of another build offers a
reload. A limit the page doesn't hold anyone to stays out.
"""
from mailmind.accounts.api import AI_DAILY_LIMIT_MAX, PREFERENCE_DEFAULTS
from mailmind.assistant.api import DEFAULT_DAILY_LIMIT_USD, INSTRUCTION_MAX
from mailmind.attention.api import BULK_CAP
from mailmind.compose.api import MAX_ATTACH_BYTES
from mailmind.kernel import send_window
from mailmind.mailbox.api import BULK_MAX

PAGE_LIMITS: dict[str, int] = {
    "ai_daily_limit_usd": AI_DAILY_LIMIT_MAX, # the most a person may set the AI's daily limit to, in dollars
    "attach_bytes": MAX_ATTACH_BYTES,        # what one message's files may come to, decoded
    "bulk_rows": BULK_MAX,                   # the rows one /api/bulk/file may name: the page sends more as more requests
    "instruction_chars": INSTRUCTION_MAX,    # what is read of a person's word on what a drafted reply should say
    "snooze_copies": BULK_CAP,               # the copies one /api/snooze may name: the page sends more as more requests
    "undo_send_longest": send_window.LONGEST,    # the most seconds a person may set Undo Send's window to
    "undo_send_shortest": send_window.SHORTEST,  # the fewest: the window itself is read off each send's answer, not fixed for a build
}

# What each preference is with nothing said, so the page starts from the server's own defaults and keeps no copy of them.
PAGE_PREFERENCE_DEFAULTS: dict[str, object] = {**PREFERENCE_DEFAULTS, "ai_daily_limit_usd": DEFAULT_DAILY_LIMIT_USD}
