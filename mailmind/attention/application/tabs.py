"""What a change here tells the open tabs: the event stream, as far as Attention uses it. mailmind.platform.sse's own
signature leaves a bare `dict`, which this names until the platform is typed too."""
from typing import Any, Protocol

from mailmind.platform import sse as _sse


class _EventStream(Protocol):
    def publish(self, topic: str, data: dict[str, Any] | None = None) -> str: ...


sse: _EventStream = _sse
