"""What a model may cite: only what it was shown. The ids in an answer are its own words, so they are never trusted: each
is checked against the messages that went into the prompt, and so a reply can't name, or lead the page to, a message outside
them. The two shapes are read as they always were: Ask's come loosely, and a brief's as schema-checked integers."""
from collections.abc import Collection
from typing import Any

from mailmind.kernel.parsed import is_list

GROUP_MAX = 80      # messages one of a brief's groups may hold


def in_answer(told: object, shown: Collection[int]) -> list[int]:
    """Ask's citations: each message the reply named and was shown, once, in the order it named them. Anything that doesn't
    read as an id is passed over, and a reply that holds no list cites nothing."""
    named: list[Any] = told if is_list(told) else []
    cited: list[int] = []
    for p in named:
        try:
            pk = int(p)
        except (TypeError, ValueError):
            continue
        if pk in shown and pk not in cited:
            cited.append(pk)
    return cited


def in_group(named: list[Any], shown: Collection[int]) -> list[int]:
    """One group of a brief: the integers it named that it was shown, up to GROUP_MAX."""
    return [p for p in named if isinstance(p, int) and p in shown][:GROUP_MAX]
