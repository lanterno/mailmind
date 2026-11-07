"""What an export would hold."""
from typing import Literal

from mailmind.http.schemas.common import Answer


class ExportGroup(Answer):
    account: str
    folder: str
    messages: int
    bodies: int


class ExportPlan(Answer):
    """`export.plan`: each account's folders, and how many of their messages have a body stored."""
    ok: Literal[True]
    total: int
    bodies: int
    groups: list[ExportGroup]
