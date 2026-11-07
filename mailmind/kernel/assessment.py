"""What the AI said of one message, and how it came to say it, as a value any context may hold.

`model` is the label shown beside the summary: the model asked for, or `parse-error` for what stood in. `served_by` is the
model that actually served the reply, which a server-side fallback can make another. An assessment from before these were
kept has `served_by`, `prompt_version` and `outcome` as None: they are unknown, and nothing fills them in.

`mailmind.kernel.attention` says what a verdict asks of you. It takes the fields rather than this, since what asks it holds a
row, a query's or a list's, and never an assessment."""
from dataclasses import dataclass
from typing import Any, Literal

type Outcome = Literal["assessed", "unusable", "refused"]
ASSESSED: Outcome = "assessed"      # a reply with a usable summary
UNUSABLE: Outcome = "unusable"      # a reply without one: what stands in is stored, so the message doesn't go round for ever
REFUSED: Outcome = "refused"        # the model declined, and likewise

IMPORTANCE_UNREADABLE = 2


def importance_of(told: Any) -> int:
    """An importance as 1 to 5, whatever it was told as. One that won't read as a number is 2."""
    try:
        return max(1, min(5, int(told)))
    except (TypeError, ValueError, OverflowError):
        return IMPORTANCE_UNREADABLE


@dataclass(frozen=True)
class Assessment:
    summary: str
    importance: int
    category: str
    action: str
    model: str
    served_by: str | None = None
    prompt_version: int | None = None
    outcome: Outcome | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "importance", importance_of(self.importance))
