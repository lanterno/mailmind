"""A thread summary, with no I/O: what is taken from a model's answer, each part held to its cap."""
import json
import re
from typing import Any, TypedDict


class Digest(TypedDict):
    tldr: str
    points: list[str]
    action: str
    participants: list[str]


def parse(out: str | None) -> Digest | None:
    """The summary a reply holds, or None when it holds no JSON."""
    m = re.search(r"\{.*\}", out or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    pts: list[Any] = d.get("points") if isinstance(d.get("points"), list) else []
    parts: list[Any] = d.get("participants") if isinstance(d.get("participants"), list) else []
    return {"tldr": str(d.get("tldr") or "")[:600],
            "points": [str(x)[:200] for x in pts][:6],
            "action": str(d.get("action") or "")[:200],
            "participants": [str(x)[:80] for x in parts][:12]}
