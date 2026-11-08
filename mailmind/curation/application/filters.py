"""A person's filters, and what they ask of mail as it arrives. They live in the rule book; what one says is Discovery's grammar."""
from collections.abc import Mapping
from typing import Any

from mailmind.curation.application.ports import Conditions, RuleBookRepository
from mailmind.curation.domain import filters
from mailmind.curation.domain.filters import Filter
from mailmind.curation.domain.rulebook import Rules
from mailmind.kernel.errors import NotFound, Refused
from mailmind.platform.logging import get_logger

log = get_logger("filters")


class Filters:
    def __init__(self, rules: RuleBookRepository, conditions: Conditions):
        self._rules = rules
        self._conditions = conditions

    def _checked(self, b: Mapping[str, Any]) -> tuple[str, bool, bool]:
        """What a create or an update says, or Refused in words for a person."""
        q, star, read = (b.get("q") or "").strip(), bool(b.get("star")), bool(b.get("read"))
        why = self._conditions.refusal(q)
        if why:
            raise Refused(why)
        if not star and not read:
            raise Refused("Choose what the filter does: star the mail, mark it read, or both.")
        return q, star, read

    def post(self, b: Mapping[str, Any]) -> dict[str, Any]:
        """A change /api/filters asked for, and the filters after it. Raises Refused or NotFound for one it can't make."""
        verb = b.get("verb")
        if verb == "create":
            q, star, read = self._checked(b)
            self._rules.change(lambda rules: self._add(rules, filters.new(q, star, read)))
        elif verb == "update":
            q, star, read = self._checked(b)
            self._rules.change(lambda rules: self._put(rules, {"id": b.get("id") or "", "q": q, "star": star, "read": read}))
        elif verb == "delete":
            self._rules.change(lambda rules: self._drop(rules, b.get("id")))
        else:
            raise Refused("verb must be create|update|delete")
        return {"ok": True, "filters": self._rules.load()["filters"]}

    @staticmethod
    def _add(rules: Rules, f: Filter) -> None:
        if len(rules["filters"]) >= filters.MOST:
            raise Refused(f"That's as many filters as MailMind keeps ({filters.MOST}).")
        rules["filters"] = [*rules["filters"], f]

    @staticmethod
    def _put(rules: Rules, f: Filter) -> None:
        changed = filters.replaced(rules["filters"], f)
        if changed is None:
            raise NotFound("no such filter")
        rules["filters"] = changed

    @staticmethod
    def _drop(rules: Rules, filter_id: str | None) -> None:
        kept = filters.without(rules["filters"], filter_id)
        if kept is None:
            raise NotFound("no such filter")
        rules["filters"] = kept

    def on_arrival(self, c: object, pk: int) -> dict[str, bool]:
        """What the filters ask of a message just stored, as flags, on the connection that stored it. A filter that can't be
        judged asks nothing: no filter may stop a sync."""
        try:
            kept = self._rules.load()["filters"]
            return filters.flags_of(f for f in kept if self._conditions.holds(c, pk, f["q"]))
        except Exception as e:
            log.warning("a filter could not be judged", exc_info=e)
            return {}
