"""A bulk command's plan: what its preview showed, frozen, until someone confirms it.

The preview resolves the command's filter to a set of messages. A confirm names the plan, and what is filed
is that set: not a list from the browser, whose pks a renumber may have moved onto other mail, and not the
filter run again, which would reach mail that came after the preview. A plan names each message by its key
and the folder it was seen in.

A plan can be confirmed once, within `action_plan.TTL` seconds. Nothing here is durable: a restart ends every plan, and
the person plans again.

Standard library only: python3 server.py uses it.
"""
import secrets
import threading
import time
from collections.abc import Iterable

from mailmind.assistant.domain.action_plan import KEPT, TTL, ActionPlan


class PlanBook:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._plans: dict[str, ActionPlan] = {}

    def open(self, verb: str, preset: str | None, messages: Iterable[tuple[int, str]]) -> ActionPlan:
        plan = ActionPlan(secrets.token_urlsafe(24), verb, preset, tuple(messages), time.time() + TTL)
        with self._lock:
            self._plans[plan.id] = plan
            for old in list(self._plans)[:-KEPT]:
                del self._plans[old]
        return plan

    def take(self, plan_id: str) -> ActionPlan | None:
        """The plan to carry out, which no one can then confirm again. None for one that has
        expired or that no one made."""
        with self._lock:
            plan = self._plans.pop(plan_id, None)
        return plan if plan and time.time() < plan.expires else None
