"""JSON as it parsed, off a file or a request. An isinstance can't say what a list or a dict holds, so these do: what's
in one is still the file's word for it, Any on purpose, until it is read."""
from typing import Any, TypeIs


def is_list(held: object) -> TypeIs[list[Any]]:
    return isinstance(held, list)


def is_object(held: object) -> TypeIs[dict[str, Any]]:
    """A JSON object, whose keys are strings."""
    return isinstance(held, dict)


def as_int(held: Any) -> int | None:
    """A number as a request may give it, or None for what doesn't read as one."""
    try:
        return int(held)
    except (TypeError, ValueError):
        return None
