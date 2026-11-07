"""JSON as it parsed, off a file or a request. An isinstance can't say what a list or a dict holds, so these do: what's
in one is still the file's word for it, Any on purpose, until it is read."""
from typing import Any, TypeIs

from mailmind.kernel.errors import Refused

_MOST = 2 ** 53        # what a client's numbers hold: a sum on one still fits a row's 64 bits


def is_list(held: object) -> TypeIs[list[Any]]:
    return isinstance(held, list)


def is_object(held: object) -> TypeIs[dict[str, Any]]:
    """A JSON object, whose keys are strings."""
    return isinstance(held, dict)


def as_int(held: Any) -> int | None:
    """A number as a request may give it, or None for what doesn't read as one. JSON may say Infinity, and no id is as
    large as what it may say instead."""
    try:
        number = int(held)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if abs(number) <= _MOST else None


def required_int(held: Any) -> int:
    """A number a request must give. Anything else is a malformed request."""
    number = as_int(held)
    if number is None:
        raise Refused()
    return number
