"""A stored row, as a use case reads it."""
from typing import Any, Protocol


class Row(Protocol):
    """One row, read by column name: sqlite3.Row is one, and so is a test's dict. What a column holds is the
    schema's word for it, so Any on purpose."""
    def __getitem__(self, key: str, /) -> Any: ...
