"""The limits the page holds a person to before it asks, each the server's own constant, which its use case enforces.

`just gen-api` writes them into web/src/lib/api/limits.gen.ts beside the API's types (tests/openapi_doc.py), and `just check-web`
fails when that file no longer matches, so the page keeps no copy of one: a limit that changes here changes there with the next
`just gen-api`. Nothing serves them: they are fixed for a build, as the API's types are, and a tab of another build offers a
reload. A limit the page doesn't hold anyone to stays out.
"""
from mailmind.assistant.api import INSTRUCTION_MAX
from mailmind.attention.api import BULK_CAP
from mailmind.compose.api import MAX_ATTACH_BYTES, SEND_DELAY
from mailmind.mailbox.api import BULK_MAX

PAGE_LIMITS: dict[str, int] = {
    "attach_bytes": MAX_ATTACH_BYTES,        # what one message's files may come to, decoded
    "bulk_rows": BULK_MAX,                   # the rows one /api/bulk/file may name: the page sends more as more requests
    "instruction_chars": INSTRUCTION_MAX,    # what is read of a person's word on what a drafted reply should say
    "snooze_copies": BULK_CAP,               # the copies one /api/snooze may name: the page sends more as more requests
    "undo_send_seconds": SEND_DELAY,         # how long a send waits in the outbox, which Undo Send's countdown counts
}
