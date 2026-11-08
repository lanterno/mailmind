"""A task: something a person confirmed they have to do, from a message that proposed it. It is theirs from then on, and how the
message fares changes only what it links to."""
from collections.abc import Mapping
from typing import Any

from mailmind.kernel.row import Row


def as_listed(row: Row | Mapping[str, Any]) -> dict[str, Any]:
    """A stored task as the page reads it: the copy its message has now, by key, folder and uid, and all three null once it has
    none left. A pk never leaves here."""
    there = row["folder"] is not None
    return {"id": row["id"], "account": row["account"], "title": row["title"], "due": row["due"],
            "done": row["done_at"] is not None, "key": row["message_key"] if there else None,
            "folder": row["folder"], "uid": row["uid"], "thread_id": row["thread_id"], "subject": row["subject"],
            "from_name": row["from_name"], "from_addr": row["from_addr"]}
