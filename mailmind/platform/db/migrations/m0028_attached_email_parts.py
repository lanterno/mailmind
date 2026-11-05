"""Migration 28 (2026-11-05): part numbers of a message with an email attached.

Parsing used to walk into an attached email and number its parts along with the message's own. It
now counts an attached email as one part, so for such a message the `attachments` rows name parts
by numbers that mean something else, or nothing. Those rows go, and opening the message records
them again from the body it keeps (`ingest.fetch_body`).
"""


def migrate(c):
    c.execute("DELETE FROM attachments WHERE message_pk IN "
              "(SELECT message_pk FROM attachments WHERE lower(content_type) LIKE 'message/%')")
